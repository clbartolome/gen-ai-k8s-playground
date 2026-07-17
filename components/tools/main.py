import threading

from werkzeug.serving import make_server

from common import port
from hub_app import create_app as create_hub_app
from mcp_app import create_app as create_mcp_app

HUB_PORT = port("hub", 9000)
MCP_PORT = port("mcp", 9001)


def serve(app, listen_port: int) -> None:
    server = make_server("0.0.0.0", listen_port, app, threaded=True)
    server.serve_forever()


def main() -> None:
    apps = [
        (create_hub_app(), HUB_PORT, "hub"),
        (create_mcp_app(), MCP_PORT, "mcp"),
    ]

    for app, listen_port, name in apps:
        thread = threading.Thread(
            target=serve,
            args=(app, listen_port),
            name=f"tools-{name}",
            daemon=True,
        )
        thread.start()
        print(f"tools/{name} listening on :{listen_port}")

    threading.Event().wait()


if __name__ == "__main__":
    main()
