"""Isolated real-container contract check; no external model or operator data."""

import argparse
import json
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from werkzeug.test import EnvironBuilder

MOCK_OLLAMA = """
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        body = json.dumps({'message': {'content': json.dumps({'answer': 'Synthetic smoke response'})}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer(('0.0.0.0', 11434), Handler).serve_forever()
"""


def docker(*arguments: str) -> str:
    """Run Docker synchronously and return captured stdout.

    Args:
        arguments: CLI arguments without shell interpolation.

    Returns:
        Stripped stdout; raises on Docker failure.
    """
    result = subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def request_json(
    url: str, payload: dict[str, Any] | None = None, method: str | None = None
) -> dict[str, Any]:
    """Make a bounded JSON request through the UI proxy.

    Args:
        url: Endpoint URL.
        payload: Optional JSON body.
        method: Optional explicit HTTP method.

    Returns:
        Parsed JSON response; raises on HTTP or transport errors.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def wait_for_settings(url: str) -> dict[str, Any]:
    """Wait for startup with a bounded deadline.

    Args:
        url: UI-proxied settings endpoint.

    Returns:
        Settings or raises after the startup deadline.
    """
    deadline = time.monotonic() + 30
    while True:
        try:
            return request_json(url)
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise
            threading.Event().wait(0.1)


def main() -> None:
    """Verify three independent images using private disposable containers.

    Returns:
        No value; removes only created resources unless --keep is requested.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui-source", required=True, type=Path)
    parser.add_argument("--dummy-source", required=True, type=Path)
    parser.add_argument("--ui-image", default="kio-ui:refactor-test")
    parser.add_argument("--kio1-image", default="kio1-orchestrator:refactor-test")
    parser.add_argument("--dummy-image", default="kio-dummy:refactor-test")
    parser.add_argument("--keep", action="store_true")
    options = parser.parse_args()
    for source in (options.ui_source, options.dummy_source):
        if not (source / "Dockerfile").is_file():
            parser.error(f"Not a project checkout: {source}")
    for image in (options.ui_image, options.kio1_image, options.dummy_image):
        docker("image", "inspect", image)
    prefix = f"kio-smoke-{uuid.uuid4().hex[:8]}"
    containers: list[str] = []
    success = False
    docker("network", "create", prefix)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "model": "synthetic-smoke-model",
                        "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
                        "chat_directory": "chats",
                        "temperature": 0.1,
                        "request_timeout": 10,
                        "endpoint": "http://ollama:11434",
                        "context_window_size": 16384,
                    }
                ),
                encoding="utf-8",
            )
            registry = temporary / "agents.json"
            registry.write_text(
                (options.dummy_source / "agents.dummy.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )

            def start(alias: str, image: str, *arguments: str) -> str:
                """Start a registered isolated container.

                Args:
                    alias: Private network alias.
                    image: Independently built image.
                    arguments: Runtime flags before the image name.

                Returns:
                    Unique created container name.
                """
                name = f"{prefix}-{alias}"
                containers.append(name)
                docker(
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    prefix,
                    "--network-alias",
                    alias,
                    *arguments,
                    image,
                )
                return name

            ollama_name = f"{prefix}-ollama"
            containers.append(ollama_name)
            docker(
                "run",
                "-d",
                "--rm",
                "--name",
                ollama_name,
                "--network",
                prefix,
                "--network-alias",
                "ollama",
                options.kio1_image,
                "python",
                "-c",
                MOCK_OLLAMA,
            )
            deadline = time.monotonic() + 10
            while True:
                try:
                    docker(
                        "exec",
                        ollama_name,
                        "python",
                        "-c",
                        "import socket; socket.create_connection(('127.0.0.1', 11434), timeout=1).close()",
                    )
                    break
                except subprocess.CalledProcessError:
                    if time.monotonic() >= deadline:
                        raise
                    threading.Event().wait(0.1)
            for number in range(2, 14):
                start(
                    f"kio{number}",
                    options.dummy_image,
                    "-e",
                    f"KIO_AGENT_ID=KIO{number}",
                )
            api_name = start(
                "kio1",
                options.kio1_image,
                "-v",
                f"{config}:/app/config.json:ro",
                "-v",
                f"{registry}:/app/agents.json:ro",
                "-e",
                "KIO1_AGENT_CONFIG=/app/agents.json",
            )
            ui_name = start("ui", options.ui_image, "-p", "127.0.0.1::8080")
            ports = json.loads(docker("inspect", ui_name))[0]["NetworkSettings"][
                "Ports"
            ]
            url = f"http://127.0.0.1:{ports['8080/tcp'][0]['HostPort']}"
            settings = wait_for_settings(url + "/api/settings")
            assert settings["model_name"] == "synthetic-smoke-model"
            assert settings["workflow_enabled"] is True
            assert len(settings["capability_catalog"]) == 12
            for path in (
                "/",
                "/health",
                "/static/app.js",
                "/static/style.css",
                "/static/image.png",
                "/static/trash-2.svg",
            ):
                with urllib.request.urlopen(url + path, timeout=5) as response:
                    assert response.status == 200 and response.read()
            chat = request_json(url + "/api/conversations", {}, "POST")
            answer = request_json(url + "/api/chat", {"query": "Synthetic test only"})
            assert (
                json.loads(answer["formatted"])["answer"] == "Synthetic smoke response"
            )
            restored = request_json(
                url + f"/api/conversations/{chat['id']}/activate", {}
            )
            assert len(restored["messages"]) == 2
            builder = EnvironBuilder(
                method="POST",
                data={
                    "file": (BytesIO(b"synthetic-upload"), "integration.txt"),
                    "category": "file",
                },
            )
            try:
                environment = builder.get_environ()
                upload = urllib.request.Request(
                    url + "/api/upload",
                    data=environment["wsgi.input"].read(),
                    headers={"Content-Type": environment["CONTENT_TYPE"]},
                )
                with urllib.request.urlopen(upload, timeout=5) as response:
                    filename = json.load(response)["filename"]
            finally:
                builder.close()
            assert (
                docker(
                    "exec",
                    api_name,
                    "python",
                    "-c",
                    f"from pathlib import Path; print(Path('assets/{filename}').read_text())",
                )
                == "synthetic-upload"
            )
            for mode in ("sequential", "parallel"):
                plan = {
                    "workflow_id": f"wf-smoke-{mode}",
                    "execution_mode": mode,
                    "steps": [
                        {
                            "step_id": f"s{number}",
                            "agent_id": f"KIO{number}",
                            "capability": settings["capability_catalog"][
                                f"KIO{number}"
                            ]["capabilities"][0],
                            "task": "Synthetic receipt only",
                            "depends_on": [],
                        }
                        for number in range(2, 14)
                    ],
                }
                result = request_json(
                    url + "/api/workflows", {"approved": True, "plan": plan}
                )
                assert result["status"] == "succeeded", result
                assert len(result["steps"]) == 12
                assert all(step["result"]["dummy"] for step in result["steps"])
            try:
                request_json(url + "/api/workflows", {"approved": False, "plan": plan})
                raise AssertionError("Unapproved workflow was accepted")
            except urllib.error.HTTPError as error:
                assert error.code == 400 and json.load(error)["error"]
            deleted = request_json(
                url + f"/api/conversations/{chat['id']}", method="DELETE"
            )
            assert deleted["deleted_id"] == chat["id"]
            assert request_json(url + "/api/conversations")["active_id"] != chat["id"]
            docker("rm", "-f", api_name)
            containers.remove(api_name)
            try:
                request_json(url + "/api/settings")
                raise AssertionError("Unavailable API was not reported")
            except urllib.error.HTTPError as error:
                assert error.code in (502, 504)
            start(
                "kio1",
                options.kio1_image,
                "-v",
                f"{config}:/app/config.json:ro",
                "-v",
                f"{registry}:/app/agents.json:ro",
                "-e",
                "KIO1_AGENT_CONFIG=/app/agents.json",
            )
            assert wait_for_settings(url + "/api/settings")["workflow_enabled"] is True
            success = True
            print(
                "PASS: UI, settings, assets, chat, history, upload, approval, and 24 agent deliveries"
            )
            print("PASS: API recreation recovered through the existing UI proxy")
            print(f"UI URL: {url}")
            if options.keep:
                print(f"Synthetic test stack retained. Network: {prefix}")
                print("Cleanup: docker rm -f " + " ".join(containers))
                print("Then: docker network rm " + prefix)
    except Exception:
        for name in containers:
            try:
                print(
                    f"Container diagnostics: {name}\n{docker('logs', '--tail', '10', name)}"
                )
            except subprocess.CalledProcessError:
                pass
        raise
    finally:
        if not (options.keep and success):
            for name in reversed(containers):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            subprocess.run(["docker", "network", "rm", prefix], capture_output=True)


if __name__ == "__main__":
    main()
