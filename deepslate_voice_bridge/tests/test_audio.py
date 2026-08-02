import array

from app.audio import Upsampler16to24


def _pcm(values):
    return array.array("h", values).tobytes()


def _values(pcm):
    a = array.array("h")
    a.frombytes(pcm)
    return list(a)


def test_ratio_is_two_to_three():
    up = Upsampler16to24()
    out = up.process(_pcm(list(range(0, 1601))))  # 1601 samples -> primes 1, converts 1600
    assert len(_values(out)) == 2400


def test_linear_interpolation_values():
    up = Upsampler16to24()
    out = _values(up.process(_pcm([0, 300, 600])))
    # pairs (0,300) -> outputs at phases 0,2/3 ; (300,600) -> phase 1/3, then carry
    assert out == [0, 200, 400]


def test_chunking_invariance():
    data = list(range(0, 3000, 7))
    whole = Upsampler16to24().process(_pcm(data))
    chunked = Upsampler16to24()
    parts = b""
    for i in range(0, len(data), 11):
        parts += chunked.process(_pcm(data[i : i + 11]))
    assert parts == whole


def test_empty_input():
    assert Upsampler16to24().process(b"") == b""


def test_apply_gain_scales_and_clamps():
    from app.audio import apply_gain

    pcm = _pcm([1000, -1000, 20000, -20000])
    assert _values(apply_gain(pcm, 2.0)) == [2000, -2000, 32767, -32768]
    assert apply_gain(pcm, 1.0) is pcm  # no-op passthrough


def test_levels():
    from app.audio import levels

    rms, peak = levels(_pcm([0, 16384, -16384, 0]))
    assert abs(peak - 0.5) < 0.01
    assert 0.3 < rms < 0.4
