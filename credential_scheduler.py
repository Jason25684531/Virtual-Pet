"""Visible, per-user Windows credential maintenance task."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import UUID
from xml.etree import ElementTree as ET


def installation_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:20]


class BaseCredentialScheduler(ABC):
    @abstractmethod
    def register(self, executable: Path, generation: str, expires_at: datetime) -> None: ...

    @abstractmethod
    def remove(self) -> None: ...


class WindowsCredentialScheduler(BaseCredentialScheduler):
    def __init__(self, root: Path):
        self._name = f"ECHOES-CredentialExpiry-{installation_id(root)}"

    @staticmethod
    def task_xml(executable: Path, generation: str, expires_at: datetime) -> str:
        UUID(generation)
        task = ET.Element("Task", version="1.2", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
        info = ET.SubElement(task, "RegistrationInfo")
        ET.SubElement(info, "Description").text = "ECHOES: remove application-managed credentials after 30 days."
        triggers = ET.SubElement(task, "Triggers")
        time = ET.SubElement(triggers, "TimeTrigger")
        # Task Scheduler uses seconds; round up so cleanup never runs before expiry.
        boundary = expires_at + timedelta(seconds=1) if expires_at.microsecond else expires_at
        ET.SubElement(time, "StartBoundary").text = boundary.replace(microsecond=0).isoformat()
        ET.SubElement(time, "Enabled").text = "true"
        logon = ET.SubElement(triggers, "LogonTrigger")
        ET.SubElement(logon, "UserId").text = "CURRENT_USER_SID"
        principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="Owner")
        # Registration binds both the logon trigger and principal to the same user.
        ET.SubElement(principal, "UserId").text = "CURRENT_USER_SID"
        ET.SubElement(principal, "LogonType").text = "InteractiveToken"
        ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
        settings = ET.SubElement(task, "Settings")
        for name, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                            ("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"),
                            ("Hidden", "false"), ("ExecutionTimeLimit", "PT2M")):
            ET.SubElement(settings, name).text = value
        restart = ET.SubElement(settings, "RestartOnFailure")
        ET.SubElement(restart, "Interval").text = "PT1M"
        ET.SubElement(restart, "Count").text = "3"
        action = ET.SubElement(ET.SubElement(task, "Actions", Context="Owner"), "Exec")
        ET.SubElement(action, "Command").text = str(executable.resolve())
        ET.SubElement(action, "Arguments").text = f"--expire-credentials --generation {generation}"
        ET.SubElement(action, "WorkingDirectory").text = str(executable.resolve().parent)
        return ET.tostring(task, encoding="unicode")

    def _run(self, script: str, payload: dict) -> None:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=json.dumps(payload), encoding="utf-8", capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            # PowerShell's error text can include local configuration; use a safe diagnostic.
            raise OSError("credential task operation failed")

    def register(self, executable: Path, generation: str, expires_at: datetime) -> None:
        if not executable.is_file():
            raise OSError("release executable missing")
        self._run(
            "$ErrorActionPreference='Stop'; $p=[Console]::In.ReadToEnd()|ConvertFrom-Json; "
            "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
            "$xml=$p.xml.Replace('CURRENT_USER_SID',$sid); "
            "$service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); "
            "$folder=$service.GetFolder('\\'); $folder.RegisterTask($p.name,$xml,6,$sid,$null,3,$null) | Out-Null",
            {"name": self._name, "xml": self.task_xml(executable, generation, expires_at)},
        )

    def remove(self) -> None:
        self._run(
            "$ErrorActionPreference='Stop'; $p=[Console]::In.ReadToEnd()|ConvertFrom-Json; "
            "$service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); "
            "$folder=$service.GetFolder('\\'); "
            "try { $folder.GetTask($p.name) | Out-Null } catch { if ($_.Exception.HResult -eq -2147024894) { exit 0 }; throw }; "
            "$folder.DeleteTask($p.name,0)",
            {"name": self._name},
        )
