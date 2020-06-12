import subprocess
import sys
import time
import warnings

import pytest


def _running_on_ci():
    """returns if we're currently running on a CI"""
    import os

    CI = os.environ.get("CI", "").lower()
    CONDA_BUILD = os.environ.get("CONDA_BUILD", "").lower()
    if CONDA_BUILD in {"1", "true"}:
        return True
    if CI in {"1", "true", "azure", "travis", "appveyor", "circleci"}:
        return True
    return False


RUNNING_ON_CI = _running_on_ci()
pytestmark = pytest.mark.skipif(
    RUNNING_ON_CI, reason="spectrometer tests are not run on ci"
)


def _aquire_connected_usb_spectrometers(timeout=10.0):
    """gathers the connected spectrometers for pytests"""
    # params and ids will be populated with the parametrization
    params = []
    ids = []

    _skip = ([pytest.param("?", marks=pytest.mark.skip)], ["no-spectrometer"], 0)
    if RUNNING_ON_CI:
        # don't run on CI
        return _skip

    # execute seabreeze._cli.ls() in separate python process
    cmd = [sys.executable, "-c", "import seabreeze._cli as cli; cli.ls()"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    timeout_at = time.time() + timeout
    while p.poll() is None:
        if time.time() > timeout_at:
            p.kill()
            warnings.warn("killed scan for connected spectrometers!")
        time.sleep(0.5)

    # parse output to generate parametrization
    if p.returncode == 0:
        stdout, _ = p.communicate()
        for spectrometer in stdout.splitlines():
            spectrometer = spectrometer.decode("utf8")
            # parse line from command output
            model, serial_number, backends = spectrometer.split("\t")
            backends = backends.split(",")

            # set correct marks
            marks = []
            if "cseabreeze" in backends:
                marks.append(pytest.mark.cseabreeze)
            if "pyseabreeze" in backends:
                marks.append(pytest.mark.pyseabreeze)

            # append the pytest.param
            params.append(pytest.param(serial_number, marks=marks))
            ids.append("{}:{}".format(model, serial_number))

    if not params:
        return _skip
    return params, ids, len(ids)


_SPEC_PARAMS, _SPEC_IDS, _SPEC_NUM = _aquire_connected_usb_spectrometers()


@pytest.fixture(scope="function", params=_SPEC_PARAMS, ids=_SPEC_IDS)
def serial_number(request):
    """yield serial numbers of connected spectrometers

    pytest.marks either cseabreeze or pyseabreeze dependent on support
    """
    yield request.param


@pytest.fixture(scope="class")
def backendlify(request):
    """setup the imports"""
    backend = request.param
    if backend == "any":
        pass

    elif backend in ("cseabreeze", "pyseabreeze"):
        import seabreeze

        seabreeze.use(backend)

    elif backend.startswith("pyseabreeze:"):
        _backend, _pyusb_backend = backend.split(":")
        import seabreeze

        seabreeze.use(_backend, _api_kwargs={"_pyusb_backend": _pyusb_backend})

    else:
        raise ValueError("unknown option '%s'" % backend)

    from seabreeze.backends import get_backend

    request.cls.backend = get_backend()
    yield


@pytest.fixture(scope="function")
def shutdown_api():
    try:
        yield
    finally:
        from seabreeze.spectrometers import list_devices

        api = getattr(list_devices, "_api", None)
        if api:
            api.shutdown()
            delattr(list_devices, "_api")


# noinspection PyMethodMayBeStatic
@pytest.mark.usefixtures("backendlify")
@pytest.mark.usefixtures("shutdown_api")
class TestHardware(object):
    def test_cant_find_serial(self):
        from seabreeze.spectrometers import Spectrometer, SeaBreezeError

        with pytest.raises(SeaBreezeError):
            Spectrometer.from_serial_number("i-do-not-exist")

    @pytest.mark.skipif(_SPEC_NUM == 0, reason="no spectrometers connected")
    def test_device_cleanup_on_exit(self):
        """test if opened devices cleanup correctly"""
        # noinspection PyProtectedMember
        backend_name = self.backend._backend_
        # noinspection PyProtectedMember
        from seabreeze.backends import _SeaBreezeConfig as _Config

        api_kwargs_str = ", ".join(
            "%s='%s'" % item for item in _Config["_api_kwargs"].items()
        )

        cmd = [
            sys.executable,
            "-c",
            "; ".join(
                [
                    "import seabreeze.%s as sb" % backend_name,
                    "d = sb.SeaBreezeAPI(%s).list_devices()[0]" % api_kwargs_str,
                    "d.open()",
                ]
            ),
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        assert p.returncode == 0, stderr

    def test_read_model(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        model = spec.model
        assert len(model) > 0

    def test_read_serial_number(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        serial = spec.serial_number
        assert len(serial) > 0

    @pytest.mark.xfail(reason="check if following tests work after crash")
    def test_crash_may_not_influence_following_tests(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        _ = Spectrometer.from_serial_number(serial_number)
        raise Exception("crash on purpose")

    def test_read_intensities(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        arr = spec.intensities()
        assert arr.size == spec.pixels

    def test_correct_dark_pixels(self, serial_number):
        from seabreeze.spectrometers import SeaBreezeError, Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        try:
            arr = spec.intensities(correct_dark_counts=True)
        except SeaBreezeError:
            pytest.skip("does not support dark counts")
        else:
            assert arr.size == spec.pixels

    def test_read_wavelengths(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        arr = spec.wavelengths()
        assert arr.size == spec.pixels

    def test_read_spectrum(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        arr = spec.spectrum()
        assert arr.shape == (2, spec.pixels)

    def test_max_intensity(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        value = spec.max_intensity
        assert isinstance(value, float)
        assert 0 < value < 1e8

    def test_integration_time_limits(self, serial_number):
        from seabreeze.spectrometers import Spectrometer

        spec = Spectrometer.from_serial_number(serial_number)
        low, high = spec.integration_time_micros_limits
        assert isinstance(low, int)
        assert isinstance(high, int)
        assert 0 < low < high < 2 ** 64

    def test_integration_time(self, serial_number):
        from seabreeze.spectrometers import Spectrometer, SeaBreezeError

        spec = Spectrometer.from_serial_number(serial_number)

        with pytest.raises(SeaBreezeError):
            # fail because too low
            spec.integration_time_micros(0)

        with pytest.raises(SeaBreezeError):
            # fail because too large
            spec.integration_time_micros(2 ** 62)

        with pytest.raises(SeaBreezeError):
            # fail because too large long overflow
            spec.integration_time_micros(2 ** 64)

        spec.integration_time_micros(10000)

    def test_trigger_mode(self, serial_number):
        from seabreeze.spectrometers import Spectrometer, SeaBreezeError

        spec = Spectrometer.from_serial_number(serial_number)
        spec.trigger_mode(0x00)  # <- normal mode

        with pytest.raises(SeaBreezeError):
            spec.trigger_mode(0xF0)  # <- should be unsupported for all specs
        # test again to see if the bus is locked
        spec.trigger_mode(0x00)  # <- normal mode

    @pytest.mark.skipif(_SPEC_NUM == 0, reason="no spectrometers connected")
    def test_list_devices_dont_close_opened_devices(self):
        """test list_devices() interaction with already opened devices"""
        from seabreeze.spectrometers import list_devices

        devices = list_devices()
        num_devices = len(devices)

        dev = devices[0]
        dev.open()
        assert dev.is_open is True
        devices_2 = list_devices()
        assert len(devices_2) == num_devices
        # dev.is_open is still True here...
        assert dev.is_open is True
        # but if we delete the new reference created by the second call to list_devices:
        del devices_2
        assert dev.is_open is True
