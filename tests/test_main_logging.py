import logging

import main


def test_main_configures_info_logging_before_starting_runtime(monkeypatch):
    configured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: configured.update(kwargs))
    monkeypatch.setattr(main, "_preload_onnx_runtime", lambda: None)
    monkeypatch.setattr(main, "_create_application", lambda _argv: type("App", (), {"exec_": lambda self: 0})())
    monkeypatch.setattr(main, "_configure_sigint_timer", lambda _app: None)
    monkeypatch.setattr(main, "_run_harness_mode", lambda _app: None)
    monkeypatch.setattr(main.sys, "exit", lambda _status: None)

    main.main()

    assert configured["level"] == logging.INFO
    assert "%(message)s" in configured["format"]
