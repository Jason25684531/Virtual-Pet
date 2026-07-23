from ui.js_gateway import JsGateway


class _Page:
    def __init__(self):
        self.scripts = []

    def runJavaScript(self, script):
        self.scripts.append(script)


def test_gateway_queues_until_webview_is_ready():
    page = _Page()
    gateway = JsGateway(lambda: page, "__raw__")
    gateway.call("setStatus", "ready")
    gateway.raw("window.raw = true")

    gateway.mark_ready()

    assert "setStatus" in page.scripts[0]
    assert page.scripts[1] == "window.raw = true"
