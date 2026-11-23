"""Stateful past-only EEG filtering and exactly scheduled sliding windows."""
from collections import deque
import time

import numpy as np
from scipy import signal
import torch

try:
    from .common import CHANNELS, normalize, fit_stats
except ImportError:
    from common import CHANNELS, normalize, fit_stats


class CausalWindows:
    """500 Hz volts -> causal filtering -> 250 Hz; accepts arbitrary packet sizes.

    No detrending over a future record, no zero-phase filtering, no windowwise
    filtering. Anti-alias low-pass precedes fixed-phase integer decimation.
    start_sample/end_sample use half-open RAW input sample coordinates.
    """
    def __init__(self, channels=CHANNELS, input_fs=500, target_fs=250):
        if (input_fs, target_fs) != (500, 250):
            raise ValueError('This artifact supports input 500 Hz and model 250 Hz only')
        self.channels = list(channels)
        self.input_fs, self.target_fs = input_fs, target_fs
        self.sos = np.concatenate([
            signal.butter(2, [49, 51], btype='bandstop', fs=input_fs, output='sos'),
            signal.butter(4, [1, 45], btype='bandpass', fs=input_fs, output='sos'),
            signal.butter(10, 80, btype='lowpass', fs=input_fs, output='sos')])
        self.reset()

    def reset(self):
        self.zi = np.zeros((len(self.sos), len(self.channels), 2), dtype=np.float64)
        self.raw_count = self.output_count = 0
        self.buffer = deque(maxlen=self.target_fs)

    def feed(self, chunk, channel_names=None):
        x = np.asarray(chunk, dtype=np.float64)
        if x.ndim != 2 or x.shape[0] != len(self.channels):
            raise ValueError('Expected [channels, raw_samples] in volts, matching artifact channel order')
        if channel_names is not None and list(channel_names) != self.channels:
            raise ValueError('Channel names/order differ from artifact')
        if not np.isfinite(x).all():
            raise ValueError('Non-finite EEG packet; reset stream after resolving acquisition gap')
        if not x.shape[1]:
            return []
        filtered, self.zi = signal.sosfilt(self.sos, x, axis=-1, zi=self.zi)
        offset = self.raw_count
        self.raw_count += x.shape[1]
        windows = []
        for local in range((-offset) % 2, x.shape[1], 2):
            self.buffer.append(filtered[:, local])
            self.output_count += 1
            if self.output_count >= 250 and (self.output_count-250) % 125 == 0:
                end = self.output_count*2
                windows.append(dict(window=np.stack(self.buffer, axis=-1).astype(np.float32),
                                    start_sample=end-500, end_sample=end,
                                    end_seconds=end/500.))
        return windows


def quality_reason(window, config):
    if not np.isfinite(window).all():
        return 'nonfinite'
    if np.max(np.abs(window)) > config.get('max_abs_uv', 300)*1e-6:
        return 'amplitude'
    if np.min(np.std(window, axis=-1)) < config.get('min_std_uv', .1)*1e-6:
        return 'flat_channel'
    return ''


class OnlinePredictor:
    """Live API. No labels are accepted or used for calibration/adaptation.

    Call start_record() at acquisition discontinuities; calibration/statistics
    persist, while filter/window/probability history reset. Returned probability
    confidence is a model score, not a guaranteed probability of correctness.
    """
    def __init__(self, model, config, calibration_seconds=60., spd_calibration='mean',
                 ema_alpha=.6, online_spd_rate=0., warmup_seconds=2.):
        if not 0 < ema_alpha <= 1 or not 0 <= online_spd_rate <= .05:
            raise ValueError('ema_alpha must be (0,1]; online_spd_rate [0,.05]')
        if calibration_seconds < 0 or warmup_seconds < 0 or spd_calibration not in {'mean', 'full', 'none'}:
            raise ValueError('Invalid calibration configuration')
        if calibration_seconds and calibration_seconds < warmup_seconds+9:
            raise ValueError('Calibration must contain >=16 eligible windows after filter warmup')
        self.model, self.config = model.eval(), config
        self.calibration_seconds, self.mode = float(calibration_seconds), spd_calibration
        self.ema_alpha, self.rate = ema_alpha, online_spd_rate
        self.warmup = float(warmup_seconds)
        self.filter = CausalWindows(config['channels'])
        self.cal_features = []
        self.calibrated = calibration_seconds == 0 or spd_calibration == 'none'
        self.calibration_record = None
        self.records_seen = 0
        self.events = []

    def start_record(self, record_id):
        if not self.calibrated and self.records_seen:
            raise ValueError('First record ended before completing calibration; do not silently use a future record')
        self.record_id = str(record_id)
        self.records_seen += 1
        if self.calibration_record is None and not self.calibrated:
            self.calibration_record = self.record_id
        self.filter.reset()
        self.previous_probability = None

    @torch.no_grad()
    def _feature(self, window):
        x = torch.from_numpy(normalize(window[None], self.config)).to(self.model.graph_device)
        return self.model.spd_branch.manifold_features(self.model._weighted_graph_maps(x))

    @torch.no_grad()
    def feed(self, chunk, channel_names=None):
        if not self.records_seen:
            raise RuntimeError('Call start_record(record_id) before feeding EEG')
        begin = time.perf_counter()
        packets = self.filter.feed(chunk, channel_names)
        filtering_ms = (time.perf_counter()-begin)*1000
        results = []
        for item in packets:
            begin = time.perf_counter()
            window = item.pop('window')
            reason = quality_reason(window, self.config['quality'])
            row = dict(record_id=self.record_id, **item, phase='test', quality=reason or 'ok')
            if item['end_seconds'] <= self.warmup:
                row['phase'] = 'warmup'
            elif not self.calibrated:
                row['phase'] = 'calibration'
                if not reason:
                    self.cal_features.append(self._feature(window).cpu())
                if item['end_seconds'] >= self.calibration_seconds:
                    if len(self.cal_features) < 16:
                        raise RuntimeError('Too few clean S3 calibration windows; inspect signal quality')
                    fit_stats(self.model, torch.cat(self.cal_features), 3, preserve_variance=self.mode == 'mean')
                    self.calibrated = True
                    self.events.append(dict(event='calibrated', record_id=self.record_id,
                                            end_sample=item['end_sample'], n_windows=len(self.cal_features),
                                            mode=self.mode, labels_used=False))
                    self.cal_features.clear()
            elif (self.record_id == self.calibration_record and
                  item['start_sample']/500. < self.calibration_seconds):
                row['phase'] = 'calibration_overlap_guard'
            elif reason:
                row['phase'] = 'rejected'
                self.previous_probability = None
            else:
                feature = self._feature(window)
                d = torch.tensor([3], dtype=torch.long, device=feature.device)
                aligned = self.model.spddsbnorm(feature, d)
                latent = self.model.spd_branch.logeig(aligned).to(self.model.graph_device, dtype=torch.float32)
                logits = self.model.classifier(self.model.readout(latent))
                if not torch.isfinite(logits).all():
                    raise RuntimeError('Non-finite inference logits; no class/confidence is emitted')
                raw = torch.softmax(logits/self.config['temperature'], dim=1)[0].cpu().numpy()
                prob = raw if self.previous_probability is None else (
                    self.ema_alpha*raw+(1-self.ema_alpha)*self.previous_probability)
                self.previous_probability = prob.copy()
                row.update(prediction=int(prob.argmax()), label=('easy', 'difficult')[int(prob.argmax())],
                           confidence=float(prob.max()), p_easy=float(prob[0]), p_difficult=float(prob[1]),
                           raw_prediction=int(raw.argmax()), raw_confidence=float(raw.max()),
                           raw_p_easy=float(raw[0]), raw_p_difficult=float(raw[1]),
                           spd_updated_after_prediction=False)
                if self.rate:
                    from spdnets.functionals import spd_2point_interpolation
                    bn = self.model.spddsbnorm.get_domain_obj(torch.tensor(3))
                    old = bn.running_mean_test.detach().clone()
                    candidate = feature.detach().reshape(old.shape)
                    updated = spd_2point_interpolation(old, candidate, self.rate)
                    if not torch.isfinite(updated).all():
                        raise RuntimeError('Non-finite online SPD update')
                    bn.running_mean_test.copy_(updated)
                    # Deliberately retain calibrated/source variance; no batch-one variance refit.
                    row['spd_updated_after_prediction'] = True
            if self.model.graph_device.type == 'cuda':
                torch.cuda.synchronize(self.model.graph_device)
            row['processing_ms'] = (time.perf_counter()-begin)*1000+filtering_ms/max(1, len(packets))
            results.append(row)
        self.last_packet_filter_ms = filtering_ms
        return results
