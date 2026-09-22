from unittest import mock

import pytest

from logitech_receiver import settings
from logitech_receiver.hidpp20_constants import SupportedFeature


def _setting(name, feature, device_value):
    setting = mock.Mock()
    setting.name = name
    setting.feature = feature
    setting.read.return_value = device_value
    return setting


def _device(device_settings, persisted, online=True, features=(SupportedFeature.HIRES_WHEEL,)):
    device = mock.Mock()
    device.online = online
    device._closed = False
    device.settings = device_settings
    device.persister = dict(persisted)
    device.features = {f: i for i, f in enumerate(features)}
    return device


def test_recheck_rewrites_a_setting_the_driver_overwrote():
    """hid-logitech-hidpp re-enables high-resolution scrolling from deferred connect
    work, which can land after Solaar's apply has already returned.  The re-check
    has to notice the device no longer matches the applied value and write again."""
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=True)
    device = _device([wheel], {"hires-smooth-resolution": False})

    settings._recheck_hires_wheel(device)

    wheel.read.assert_called_once_with(cached=False)
    wheel.write.assert_called_once_with(False, save=False)


def test_recheck_leaves_a_setting_that_still_matches():
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=False)
    device = _device([wheel], {"hires-smooth-resolution": False})

    settings._recheck_hires_wheel(device)

    wheel.write.assert_not_called()


@pytest.mark.parametrize(
    "persisted",
    [
        {},
        {"hires-smooth-resolution": None},
        {"hires-smooth-resolution": False, "_sensitive": {"hires-smooth-resolution": settings.SENSITIVITY_IGNORE}},
    ],
    ids=["no-saved-value", "saved-value-is-none", "setting-marked-ignore"],
)
def test_recheck_skips_when_there_is_nothing_to_assert(persisted):
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=True)
    device = _device([wheel], persisted)

    settings._recheck_hires_wheel(device)

    wheel.write.assert_not_called()


def test_recheck_ignores_other_features():
    """Only HIRES_WHEEL settings are re-checked; the rest are left to the normal apply."""
    other = _setting("dpi", SupportedFeature.ADJUSTABLE_DPI, device_value=800)
    device = _device([other], {"dpi": 1200})

    settings._recheck_hires_wheel(device)

    other.read.assert_not_called()
    other.write.assert_not_called()


@pytest.mark.parametrize("online, closed", [(False, False), (True, True)], ids=["offline", "closed"])
def test_recheck_does_not_touch_a_gone_device(online, closed):
    """The re-check fires from a timer, so the device may have gone away meanwhile."""
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=True)
    device = _device([wheel], {"hires-smooth-resolution": False}, online=online)
    device._closed = closed

    settings._recheck_hires_wheel(device)

    wheel.read.assert_not_called()
    wheel.write.assert_not_called()


def test_recheck_survives_a_failing_read():
    """A device that errors mid-re-check must not take down the timer thread."""
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=True)
    wheel.read.side_effect = OSError("gone")
    device = _device([wheel], {"hires-smooth-resolution": False})

    settings._recheck_hires_wheel(device)  # must not raise

    wheel.write.assert_not_called()


def test_apply_all_settings_schedules_a_recheck_for_hires_wheel_devices(mocker):
    wheel = _setting("hires-smooth-resolution", SupportedFeature.HIRES_WHEEL, device_value=False)
    device = _device([wheel], {"hires-smooth-resolution": False})
    mocker.patch.object(settings.time, "sleep")
    schedule = mocker.patch.object(settings, "_schedule_hires_wheel_recheck")

    settings.apply_all_settings(device)

    wheel.apply.assert_called_once()
    schedule.assert_called_once_with(device)


def test_apply_all_settings_skips_the_recheck_without_hires_wheel(mocker):
    other = _setting("dpi", SupportedFeature.ADJUSTABLE_DPI, device_value=1200)
    device = _device([other], {"dpi": 1200}, features=(SupportedFeature.ADJUSTABLE_DPI,))
    schedule = mocker.patch.object(settings, "_schedule_hires_wheel_recheck")

    settings.apply_all_settings(device)

    schedule.assert_not_called()


def test_only_one_recheck_is_pending_per_device(mocker):
    """Repeated reconnects must not pile up timers."""
    device = _device([], {})
    mocker.patch.object(settings, "HIRES_WHEEL_RECHECK_DELAY", 60)

    settings._schedule_hires_wheel_recheck(device)
    first = device._hires_wheel_recheck
    settings._schedule_hires_wheel_recheck(device)
    second = device._hires_wheel_recheck

    assert first is not second
    assert not first.is_alive()
    settings.cancel_hires_wheel_recheck(device)
    assert device._hires_wheel_recheck is None
