# PyInstaller build: one executable with the template inside.
#
#   uv run pyinstaller packaging/loftline.spec
#
# The bundle directory (copier.yml, credentials.yml, template/, infra/) is
# packed as data under _bundle/, where cli/loftline/paths.py finds it.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).resolve().parent

datas = [
    # The package's own data file, which PyInstaller does not pick up alone.
    (str(root / "cli" / "loftline" / "features.yml"), "loftline"),
    (str(root / "copier.yml"), "_bundle"),
    (str(root / "credentials.yml"), "_bundle"),
    (str(root / "template"), "_bundle/template"),
    (str(root / "infra"), "_bundle/infra"),
]
binaries = []
hiddenimports = collect_submodules("loftline") + collect_submodules("copier")
for package in (
    "copier",
    "jinja2",
    "jinja2_ansible_filters",
    "yaml",
    "pydantic",
    "mcp",
    "typer",
    "plumbum",
    "questionary",
    "pathspec",
    "dunamai",
    "funcy",
    "packaging",
):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "cli")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="loftline",
    console=True,
    upx=False,
)
