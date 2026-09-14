"""Nothing may load the kernel library until a probe child has survived it.

#911 moved the crash into a child process, and on the Celeron J4125 it changed
nothing: `python -c "import immich_memories.titles.kernel_backend_probe"` still
exited 132, because importing the probe imported the library it was meant to be
asking about. The probe cannot run if reaching it is already the crash.

Every test here runs in a child interpreter. A warm one that has already loaded
the library somewhere else would answer "imported" for reasons that have nothing
to do with the module under test.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

_GATED_MODULES = (
    "immich_memories.titles.kernel_backend_probe",
    "immich_memories.titles.rendering_service",
    "immich_memories.preflight",
    "immich_memories.titles.generator",
    "immich_memories.tracking.system_info",
    "immich_memories.cli",
)


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_modules_that_decide_about_kernels_do_not_load_them() -> None:
    completed = _run(
        f"""
        import importlib
        import sys

        for name in {list(_GATED_MODULES)!r}:
            importlib.import_module(name)
            assert "quadrants" not in sys.modules, name
        print("clean")
        """
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "clean"


def test_a_child_killed_by_sigill_leaves_the_parent_on_pil_and_unloaded() -> None:
    """The no-AVX box, reproduced at the only boundary a Mac can reproduce it at.

    Exit 132 is what a shell or a container init reports for SIGILL. The parent
    has to come out of that with the PIL renderer and without the library in
    `sys.modules` — which is the whole point, since loading it is the death.
    """
    completed = _run(
        """
        import subprocess
        import sys
        from types import SimpleNamespace

        from immich_memories.operations import bounded_process

        # WHY: the probe child is the process boundary, and no machine here has a
        # processor that dies on a kernel. This is the exit code one reports.
        bounded_process.run_bounded_process = lambda command, *, timeout, **kwargs: (
            subprocess.CompletedProcess(list(command), 132, "", "")
        )

        from immich_memories.titles.rendering_service import RenderingService

        service = RenderingService(SimpleNamespace(use_gpu_rendering=True))

        assert not service.use_gpu
        assert service.backend is None
        assert "quadrants" not in sys.modules, "the parent loaded the library anyway"
        print("pil")
        """
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "pil"
