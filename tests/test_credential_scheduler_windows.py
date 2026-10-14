from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from credential_scheduler import WindowsCredentialScheduler


@pytest.mark.parametrize("microsecond", [0, 123456])
def test_task_boundary_never_rounds_expiry_down(tmp_path, microsecond):
    expires = datetime(2026, 10, 14, 12, 0, 0, microsecond, tzinfo=timezone.utc)
    xml = WindowsCredentialScheduler.task_xml(tmp_path / "VirtualPet.exe", str(uuid4()), expires)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    boundary = datetime.fromisoformat(ET.fromstring(xml).find("t:Triggers/t:TimeTrigger/t:StartBoundary", ns).text)
    assert timedelta(0) <= boundary - expires < timedelta(seconds=1)
    assert boundary.microsecond == 0


@pytest.mark.skipif(os.name != "nt" or os.getenv("RUN_WINDOWS_SCHEDULER_TESTS") != "1",
                    reason="Explicit per-user Windows task registration smoke test")
def test_current_user_can_register_export_and_remove_visible_task(tmp_path):
    scheduler = WindowsCredentialScheduler(tmp_path)
    try:
        scheduler.register(Path(sys.executable), str(uuid4()), datetime.now(timezone.utc) + timedelta(days=30))
        scheduler._run(
            "$ErrorActionPreference='Stop'; $p=[Console]::In.ReadToEnd()|ConvertFrom-Json; "
            "$t=Get-ScheduledTask -TaskName $p.name; "
            "if ($t.Settings.Hidden -or !$t.Settings.StartWhenAvailable -or $t.Principal.RunLevel -ne 'Limited') { throw 'invalid task' }",
            {"name": scheduler._name},
        )
    finally:
        scheduler.remove()
