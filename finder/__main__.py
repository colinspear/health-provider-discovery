import argparse
import os
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="In-network provider finder")
    ap.add_argument("--demo", action="store_true", help="fictional data, no network needed")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.demo:
        os.environ["HPD_DEMO"] = "1"
    url = f"http://127.0.0.1:{a.port}"
    print(f"Provider finder running at {url}")
    if not a.no_browser:
        webbrowser.open(url)
    uvicorn.run("finder.app:create_app", factory=True, host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
