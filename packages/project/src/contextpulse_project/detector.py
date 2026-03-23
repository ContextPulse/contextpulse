"""ActiveProjectDetector — determines which project is currently in focus."""

from pathlib import Path

from contextpulse_project.registry import ProjectRegistry


class ActiveProjectDetector:
    def __init__(self, registry: ProjectRegistry, projects_root: Path | None = None):
        self.registry = registry
        self.projects_root = projects_root or Path.home() / "Projects"

    def detect(
        self,
        cwd: str | None = None,
        window_title: str | None = None,
        app_name: str | None = None,
    ) -> str | None:
        # 1. CWD-based detection
        if cwd:
            cwd_path = Path(cwd).resolve()
            try:
                rel = cwd_path.relative_to(self.projects_root.resolve())
                project_name = rel.parts[0] if rel.parts else None
                if project_name and self.registry.get(project_name):
                    return project_name
            except ValueError:
                pass

        # 2. Window title contains project directory name
        if window_title:
            title_lower = window_title.lower()
            for project in self.registry.list_all():
                if project.name.lower() in title_lower:
                    return project.name
                for alias in project.aliases:
                    if alias.lower() in title_lower:
                        return project.name

        return None
