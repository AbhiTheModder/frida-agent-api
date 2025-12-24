import json
import logging
import os
import re
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

FRIDA_BRIDGE_RE = re.compile(
    r"""import(?:\s+[^"']+\s+from\s+)?["'](frida-[^"']+-bridge)["']""",
    re.VERBOSE,
)

BRIDGE_MAPPINGS = {
    "Java": ("frida-java-bridge", 'import Java from "frida-java-bridge";'),
    "ObjC": ("frida-objc-bridge", 'import ObjC from "frida-objc-bridge";'),
    "Swift": ("frida-swift-bridge", 'import Swift from "frida-swift-bridge";'),
}


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


def find_frida_bridges(source: str) -> set[str]:
    return set(m.group(1) for m in FRIDA_BRIDGE_RE.finditer(source))


def inject_missing_bridges(source: str) -> str:
    """
    Scans the source for usage of Java, ObjC, or Swift.
    If used but not imported, injects the import statement at the top.
    """
    existing_imports = find_frida_bridges(source)
    injections = []

    for keyword, (pkg_name, import_stmt) in BRIDGE_MAPPINGS.items():
        if pkg_name not in existing_imports:
            if re.search(rf"\b{keyword}\b", source):
                logger.info(
                    f"Detected usage of '{keyword}' without import. Injecting: {pkg_name}"
                )
                injections.append(import_stmt)

    if injections:
        return "\n".join(injections) + "\n" + source

    return source


def collect_bridge_deps(agent_dir: Path) -> set[str]:
    deps: set[str] = set()
    for path in agent_dir.rglob("*.ts"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        deps |= find_frida_bridges(text)
    for path in agent_dir.rglob("*.js"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        deps |= find_frida_bridges(text)
    return deps


@app.get("/")
def index():
    return static_file("index.html", root="./public", mimetype="text/html")


@app.get("/frida_ver")
def frida_ver():
    return run_command(["frida", "--version"], "./").stdout.strip()


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
            snippet = inject_missing_bridges(snippet)
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
                for ts_file in agent_dir.rglob("*.ts"):
                    try:
                        original_text = ts_file.read_text(
                            encoding="utf-8", errors="ignore"
                        )
                        fixed_text = inject_missing_bridges(original_text)
                        if fixed_text != original_text:
                            ts_file.write_text(fixed_text, encoding="utf-8")
                    except Exception as e:
                        logger.warning(
                            f"Failed to auto-inject imports for {ts_file}: {e}"
                        )

            else:
                text_content = content.decode("utf-8", errors="ignore")
                text_content = inject_missing_bridges(text_content)

                with open(agent_dir / "index.ts", "w") as f:
                    f.write(text_content)

        manager = "bun" if shutil.which("bun") else "npm"
        run_command([manager, "install", "--ignore-scripts"], cwd=str(output_dir))

        bridge_deps = collect_bridge_deps(agent_dir)
        logger.info(f"Detected frida bridge deps: {bridge_deps}")

        if bridge_deps:
            if manager == "bun":
                run_command(["bun", "add", *bridge_deps], cwd=str(output_dir))
            else:
                run_command(["npm", "install", *bridge_deps], cwd=str(output_dir))

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
