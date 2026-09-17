from cx_Freeze import setup, Executable

build_options = {
    "packages": [
        "modules",
        "textual",
        "rich",
    ],
    "includes": [
        "textual.widgets._tab_pane",
    ],
}

setup(
    name="questy",
    version="1.0.0",
    options={
        "build_exe": build_options,
    },
    executables=[
        Executable(
            "questy.py",
            target_name="questy",
        )
    ],
)