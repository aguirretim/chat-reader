import numpy as np

from chatreader.audio import shape_samples


def test_samples_above_full_scale_are_held_at_the_limit():
    loud = np.array([0.0, 0.5, 1.3, -1.7, 0.99, -1.0], dtype=np.float32)
    out = shape_samples(loud, 100)
    assert out.dtype == np.float32
    assert out.max() <= 1.0 and out.min() >= -1.0
    assert np.allclose(out, [0.0, 0.5, 1.0, -1.0, 0.99, -1.0])


def test_volume_is_applied_before_the_limit():
    out = shape_samples(np.array([1.6, -0.8], dtype=np.float32), 50)
    assert np.allclose(out, [0.8, -0.4])           # 1.6 at half volume fits, so it is not flattened


def test_volume_outside_0_to_100_is_treated_as_the_nearest_end():
    one = np.array([0.5], dtype=np.float32)
    assert np.allclose(shape_samples(one, 250), [0.5]) and np.allclose(shape_samples(one, -5), [0.0])


def test_a_plain_list_from_the_voice_engine_is_accepted():
    assert np.allclose(shape_samples([0.25, 2.0], 100), [0.25, 1.0])
