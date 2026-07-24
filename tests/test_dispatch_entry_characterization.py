"""Every directive reaches ActionBus, including dynamic motion keys."""

from types import SimpleNamespace

from ui.transparent_window import TransparentWindow


class _Bus:
    def __init__(self): self.commands = []
    def execute(self, command):
        self.commands.append(command)
        return SimpleNamespace(status="ok")


def test_dynamic_motion_uses_action_bus_without_inspecting_dispatcher_bindings():
    bus = _Bus()
    window = SimpleNamespace(_action_bus=bus)

    assert TransparentWindow.dispatch_action(window, "[ACTION:music_idle]") is True
    assert TransparentWindow.dispatch_action(window, "[ACTION:music_idle]") is True

    assert [command.action for command in bus.commands] == ["music_idle", "music_idle"]
