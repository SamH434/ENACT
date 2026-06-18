"""
Native window dashboard for ENACT.

Renders the same rich-based dashboard as live.py, but displays it in a real
desktop window via pywebview. The window uses the WebView2 control on Windows
(the same engine as Edge), so it's hardware-accelerated and looks like any
other Win11 application.

Architecture:
    1. A tiny local HTTP server (built into pywebview via its js_api) serves
       freshly-rendered HTML frames of the dashboard on demand.
    2. The window loads an initial page with a JavaScript loop that polls
       for the latest frame every REFRESH_MS milliseconds.
    3. Each poll triggers render_html_frame() in live.py, which builds the
       layout and exports it as HTML.

The terminal version (live.py main()) is unchanged and still works. This is
purely an additional way to display the same dashboard, not a replacement.

Run with:
    python -m src.dashboard.window
"""

import webview

from src.dashboard.live import render_html_frame


# how often the window polls for a fresh dashboard frame, in milliseconds.
# matches the terminal version's 20Hz refresh for consistency
REFRESH_MS = 50

# window dimensions chosen to fit the dashboard's natural 200x60 terminal size
# at the font size set in live.py's HTML_TEMPLATE
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 820


"""
JavaScript-callable API exposed to the embedded browser.

pywebview lets you expose Python methods to JavaScript running in the window.
The browser-side JS polls get_frame() at REFRESH_MS intervals and swaps the
rendered HTML into the DOM. This is the cleanest pattern for "Python renders,
browser displays" without standing up a real HTTP server.
"""
class DashboardAPI:

    # called by the window's JS poll loop, returns one fresh frame's HTML
    def get_frame(self) -> str:
        return render_html_frame(width=140, height=38)


# the initial HTML loaded into the window. it polls the Python side for frames
# and swaps each one in. the loading-state styling matches the dashboard theme
# so the brief flash before the first frame doesn't look out of place
_INITIAL_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>ENACT</title>
<style>
html, body {
    margin: 0;
    padding: 0;
    background: #000000;
    color: #d7af00;
    font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    overflow: hidden;
    height: 100vh;
}
#root {
    width: 100%;
    height: 100%;
}
.loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: #00afff;
}
</style>
</head>
<body>
<div id="root"><div class="loading">[ ENACT · INITIALIZING ]</div></div>
<script>
    // poll Python for a fresh dashboard frame every REFRESH_MS ms.
    // pywebview exposes the DashboardAPI methods on window.pywebview.api
    async function tick() {
        try {
            const html = await window.pywebview.api.get_frame();
            // the returned HTML is a full document. we only need its body,
            // so we parse it and swap the contents of #root with the body.
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            document.getElementById('root').innerHTML = doc.body.innerHTML;
        } catch (e) {
            // pywebview takes a moment to initialize, errors during that
            // window are expected. quietly retry next tick.
        }
    }
    // pywebviewready fires once the bridge is available
    window.addEventListener('pywebviewready', () => {
        tick();  // first frame immediately so we don't show "initializing" too long
        setInterval(tick, REFRESH_MS_PLACEHOLDER);
    });
</script>
</body>
</html>
"""


# entry point: opens the dashboard window and runs until the user closes it
def main() -> None:
    # inject the python-side refresh rate into the JS so it's a single source of truth
    initial_html = _INITIAL_HTML.replace("REFRESH_MS_PLACEHOLDER", str(REFRESH_MS))

    api = DashboardAPI()
    webview.create_window(
        title="ENACT — Network Resilience Telemetry",
        html=initial_html,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
        background_color="#000000",
    )
    window.events.shown += lambda: window.maximize()
    # gui='edgechromium' forces WebView2 on Windows even if other backends are
    # installed. it's the modern, accelerated choice on Win11.
    webview.start(gui="edgechromium", icon="docs/assets/enact_icon.png")


if __name__ == "__main__":
    main()