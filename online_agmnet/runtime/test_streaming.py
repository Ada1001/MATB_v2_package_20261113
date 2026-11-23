"""Meaningful causality, packet-invariance and deployment-state checks."""
import copy

import numpy as np
import pytest
import torch

from online_agmnet.common import CHANNELS, build_model, seed_all
from online_agmnet.streaming import CausalWindows, OnlinePredictor


def test_packet_boundaries_do_not_change_filtered_windows():
    raw = np.random.default_rng(42).normal(0, 10e-6, (62, 2257))
    whole = CausalWindows().feed(raw)
    processor = CausalWindows()
    packets = []
    start = 0
    for length in [1, 19, 503, 2, 37, 700, 995]:
        packets.extend(processor.feed(raw[:, start:start+length]))
        start += length
    assert start == raw.shape[1]
    assert [r['end_sample'] for r in packets] == list(range(500, 2258, 250))
    for a, b in zip(whole, packets):
        np.testing.assert_allclose(a['window'], b['window'], rtol=0, atol=0)


def test_future_changes_cannot_change_already_available_windows():
    raw = np.random.default_rng(2).normal(0, 10e-6, (62, 1750))
    other = raw.copy()
    other[:, 1000:] *= 1e3
    original = CausalWindows().feed(raw)
    changed = CausalWindows().feed(other)
    for a, b in zip(original, changed):
        if a['end_sample'] <= 1000:
            np.testing.assert_array_equal(a['window'], b['window'])


def tiny_model():
    seed_all()
    config = dict(channels=CHANNELS, target_fs=250, architecture=dict(temporal_hidden=8,
                  graph_hidden=8, fusion_dim=16, subspacedims=4), temperature=1.,
                  normalizer=dict(center=[0.]*62, scale=[10e-6]*62),
                  quality=dict(max_abs_uv=300, min_std_uv=.1))
    return build_model(config).eval(), config


def test_calibration_is_past_only_and_scored_windows_do_not_overlap_it():
    model, config = tiny_model()
    before = {k: v.clone() for k, v in model.state_dict().items() if 'dom 1' in k or 'dom 2' in k}
    params = {k: v.clone() for k, v in model.named_parameters()}
    live = OnlinePredictor(model, config, calibration_seconds=12)
    live.start_record('first')
    raw = np.random.default_rng(5).normal(0, 10e-6, (62, 7500))
    rows = []
    for start in range(0, raw.shape[1], 50):
        rows.extend(live.feed(raw[:, start:start+50]))
    assert live.events[0]['end_sample'] == 6000
    assert live.events[0]['labels_used'] is False
    scored = [r for r in rows if r['phase'] == 'test']
    assert scored and min(r['start_sample'] for r in scored) >= 6000
    assert not any('prediction' in r for r in rows if r['phase'] != 'test')
    for name, value in params.items():
        torch.testing.assert_close(dict(model.named_parameters())[name], value, rtol=0, atol=0)
    for name, value in before.items():
        torch.testing.assert_close(model.state_dict()[name], value, rtol=0, atol=0)


def test_optional_spd_update_occurs_after_current_prediction():
    base, config = tiny_model()
    adapting = copy.deepcopy(base)
    a = OnlinePredictor(base, config, calibration_seconds=0, online_spd_rate=0)
    b = OnlinePredictor(adapting, config, calibration_seconds=0, online_spd_rate=.01)
    a.start_record('r')
    b.start_record('r')
    raw = np.random.default_rng(8).normal(0, 10e-6, (62, 1250))
    ra, rb = a.feed(raw), b.feed(raw)
    first_a = next(r for r in ra if r['phase'] == 'test')
    first_b = next(r for r in rb if r['phase'] == 'test')
    assert first_a['raw_p_easy'] == first_b['raw_p_easy']
    assert first_b['spd_updated_after_prediction']


def test_bad_packets_and_unfinished_calibration_fail_explicitly():
    processor = CausalWindows()
    with pytest.raises(ValueError, match='Non-finite'):
        processor.feed(np.full((62, 20), np.nan))
    model, config = tiny_model()
    predictor = OnlinePredictor(model, config)
    predictor.start_record('one')
    with pytest.raises(ValueError, match='ended before'):
        predictor.start_record('two')


def test_full_default_agmnet_backpropagates_with_domain_balanced_batch():
    seed_all()
    config = dict(channels=CHANNELS, target_fs=250, architecture=dict(
        temporal_hidden=64, graph_hidden=64, subspacedims=20, fusion_dim=128))
    model = build_model(config).train()
    x = torch.randn(4, 62, 250)
    logits = model(x, torch.tensor([1, 1, 2, 2]))
    assert logits.shape == (4, 2)
    loss = torch.nn.functional.cross_entropy(logits, torch.tensor([0, 1, 0, 1]))
    loss.backward()
    assert torch.isfinite(loss)
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
