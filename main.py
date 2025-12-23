import json
import logging
import os
import shutil
import subprocess
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import gettempdir

from bottle import Bottle, HTTPError, request, run, static_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Bottle()


def run_command(command: list, cwd: str):
    """
    Helper to run shell commands safely.
    """
    try:
        logger.info(f"Running command: {' '.join(command)} in {cwd}")

        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        return result
    except subprocess.CalledProcessError as e:
        error_detail = e.output if e.output else "No output captured from process."
        logger.error(f"Command failed with exit code {e.returncode}: {error_detail}")

        raise HTTPError(500, f"Build step failed:\n{error_detail}")


def cleanup_directory(path: str):
    """
    Remove the temporary build directory.
    """
    try:
        shutil.rmtree(path)
        logger.info(f"Cleaned up temporary directory: {path}")
    except Exception as e:
        logger.error(f"Failed to cleanup directory {path}: {e}")

@app.get("/")
def index():
    return static_file("index.html", root="./public", mimetype="text/html")

@app.post("/compile")
def compile_agent():
    """
    POST /compile

    Accepts multipart/form-data:
      - file: optional file upload (.ts file or .zip with multiple .ts/.js, must contain index.ts)
      - snippet: optional text form field with TS/JS snippet

    Exactly one of `file` or `snippet` must be provided.
    Returns compiled _agent.js as application/javascript.
    """
    upload = request.files.get("file")
    snippet = request.forms.get("snippet")

    if upload and snippet:
        raise HTTPError(400, "Provide either a file or a snippet.")
    if not upload and not snippet:
        raise HTTPError(
            400,
            "No input provided. Upload a .ts file or a zip file containig multiple .ts scripts or provide a snippet.",
        )

    run_id = str(uuid.uuid4())
    build_root = Path(gettempdir()) / "frida_builds" / run_id
    output_dir = build_root / "output"
    agent_dir = output_dir / "agent"

    try:
        os.makedirs(build_root, exist_ok=True)

        run_command(
            ["frida-create", "-t", "agent", "-o", "output"], cwd=str(build_root)
        )

        tsconfig_path = output_dir / "tsconfig.json"
        if tsconfig_path.exists():
            with open(tsconfig_path, "r") as f:
                config = json.load(f)

            if "compilerOptions" in config:
                config["compilerOptions"]["strict"] = False

            with open(tsconfig_path, "w") as f:
                json.dump(config, f, indent=2)
            logger.info("Successfully disabled strict mode in tsconfig.json")

        if agent_dir.exists():
            for ts_file in agent_dir.glob("*.ts"):
                ts_file.unlink()
        else:
            os.makedirs(agent_dir, exist_ok=True)

        if snippet:
            with open(agent_dir / "index.ts", "w") as f:
                f.write(snippet)

        elif upload:
            filename = upload.filename or ""
            content = upload.file.read()

            if filename.endswith(".zip"):
                with zipfile.ZipFile(BytesIO(content)) as z:
                    for member in z.infolist():
                        inner_name = os.path.basename(member.filename)
                        if not inner_name:
                            continue
                        if inner_name.endswith(".ts") or inner_name.endswith(".js"):
                            source = z.open(member)
                            target_path = agent_dir / inner_name
                            with open(target_path, "wb") as target:
                                shutil.copyfileobj(source, target)

                if not (agent_dir / "index.ts").exists():
                    raise HTTPError(400, "ZIP must contain an index.ts file.")
            else:
                with open(agent_dir / "index.ts", "wb") as f:
                    f.write(content)

        manager = "bun" if shutil.which("bun") else "npm"
        run_command([manager, "install"], cwd=str(output_dir))

        run_command(
            ["frida-compile", "agent/index.ts", "-o", "_agent.js", "-c"],
            cwd=str(output_dir),
        )

        final_artifact = output_dir / "_agent.js"

        response = static_file(
            final_artifact.name,
            root=str(output_dir),
            mimetype="application/javascript",
            download=final_artifact.name,
        )

        cleanup_directory(str(build_root))

        return response

    except HTTPError:
        cleanup_directory(str(build_root))
        raise
    except Exception as e:
        cleanup_directory(str(build_root))
        raise HTTPError(500, str(e))


if __name__ == "__main__":
    run(app, host="0.0.0.0", port=8000, debug=False)
