"""What the executor acts through besides Docker: Coolify, and the deployment repository.

Like the Docker calls, each of these is a fixed request built from the rules file. A plan
chooses a name the owner wrote there; it never supplies a URL, an id, a path or a command.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import yaml

from paeon import proc
from paeon.catalog import Proposal, Step
from paeon.rules import Coolify, Repository


class ActuatorError(Exception):
    """An action could not be applied. The message is safe to record and to show."""


# --- Coolify ----------------------------------------------------------------------------------


class CoolifyActuator:
    """Restart of a resource through Coolify's API, so that Coolify knows what happened to it."""

    def __init__(self, http: httpx.Client, config: Coolify) -> None:
        self._http = http
        self._resources = config.resources

    def _path(self, name: str) -> str:
        resource = self._resources[name]
        return f"/api/v1/{resource.kind}s/{resource.uuid}"

    def restart(self, name: str) -> None:
        try:
            response = self._http.post(f"{self._path(name)}/restart")
        except httpx.HTTPError as exc:
            raise ActuatorError(f"coolify is unreachable: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ActuatorError(f"coolify answered HTTP {response.status_code}")

    def status(self, name: str) -> str | None:
        """Coolify's own word on the resource (`running:healthy`), or None if it gives none."""
        try:
            response = self._http.get(self._path(name))
        except httpx.HTTPError as exc:
            raise ActuatorError(f"coolify is unreachable: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ActuatorError(f"coolify answered HTTP {response.status_code}")
        status = response.json().get("status")
        return status if isinstance(status, str) and status else None

    def close(self) -> None:
        self._http.close()


# --- a value in a YAML file -------------------------------------------------------------------

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+=-]*")


def _node(root: yaml.Node, key: str) -> yaml.Node:
    node = root
    for part in key.split("."):
        if isinstance(node, yaml.MappingNode):
            found = [value for name, value in node.value if name.value == part]
            if len(found) != 1:
                raise ActuatorError(f"the file has no single key {key}")
            node = found[0]
        elif isinstance(node, yaml.SequenceNode) and part.isdigit() and int(part) < len(node.value):
            node = node.value[int(part)]
        else:
            raise ActuatorError(f"the file has no key {key}")
    return node


def edit_yaml_scalar(text: str, key: str, value: str) -> str:
    """The text with the one value at a dotted key replaced, and nothing else touched.

    The file is not re-written: the value's exact position is found in the parsed document and
    only those characters change, so comments, order and layout stay as their author left
    them. The key must exist and hold a single value. The result is parsed again and compared:
    if anything but that value differs, the edit is refused.
    """
    if "\n" in value or "\r" in value or _PLAIN.fullmatch(value) is None:
        raise ActuatorError("the new value is not a plain single value")
    try:
        root = yaml.compose(text)
        before = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ActuatorError("the file is not valid YAML") from None
    if root is None:
        raise ActuatorError("the file is empty")
    node = _node(root, key)
    if not isinstance(node, yaml.ScalarNode) or node.style in ("|", ">"):
        raise ActuatorError(f"{key} does not hold a single value")
    quote = node.style if node.style in ("'", '"') else ""
    start, end = node.start_mark.index, node.end_mark.index
    edited = text[:start] + f"{quote}{value}{quote}" + text[end:]

    def at(document: Any) -> Any:
        for part in key.split("."):
            document = document[int(part)] if isinstance(document, list) else document[part]
        return document

    def without(document: Any) -> Any:
        """The document with the value blanked, to compare everything else."""
        parts = key.split(".")
        holder = document
        for part in parts[:-1]:
            holder = holder[int(part)] if isinstance(holder, list) else holder[part]
        if isinstance(holder, list):
            holder[int(parts[-1])] = None
        else:
            holder[parts[-1]] = None
        return document

    try:
        after = yaml.safe_load(edited)
        changed = at(after)
        same = without(after) == without(before)
    except (yaml.YAMLError, KeyError, IndexError, TypeError, ValueError):
        raise ActuatorError("the edit would break the file") from None
    if not same or str(changed) != value:
        raise ActuatorError("the edit would change more than that value")
    return edited


# --- the deployment repository ----------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    branch: str
    url: str | None  # the pull request, when one could be opened
    files: tuple[str, ...]


class RepositoryActuator:
    """Turns configuration changes into a branch and a pull request. It never merges."""

    def __init__(
        self,
        config: Repository,
        *,
        token: str | None = None,
        run: proc.Runner = proc.run,
        http: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._token = token
        self._run = run
        self._http = http

    def _git(self, *args: str, env: Mapping[str, str]) -> str:
        argv = [*proc.GIT, "-C", self._config.path, *args]
        try:
            code, output = self._run(argv, timeout=120, env=env)
        except Exception as exc:
            raise ActuatorError(f"git {args[0]} could not be run: {type(exc).__name__}") from None
        if code != 0:
            raise ActuatorError(f"git {args[0]} failed")
        return output

    def open(self, proposal_id: int, proposal: Proposal, steps: Sequence[Step]) -> Change:
        config = self._config
        env = {
            **proc.GIT_ENV,
            "GIT_AUTHOR_NAME": config.author_name,
            "GIT_AUTHOR_EMAIL": config.author_email,
            "GIT_COMMITTER_NAME": config.author_name,
            "GIT_COMMITTER_EMAIL": config.author_email,
        }
        if config.identity_file or config.known_hosts_file:
            ssh = proc.ssh_command(config.identity_file, config.known_hosts_file)
            env["GIT_SSH_COMMAND"] = " ".join(ssh)
        branch = f"paeon/incident-{proposal.incident_id}-{proposal.hash[:8]}"
        self._git("fetch", "--quiet", config.remote, config.base, env=env)
        # The clone is Paeon's own: every change starts from the remote's base, never from
        # whatever an earlier change left behind.
        self._git("checkout", "--quiet", "--force", "-B", branch, "FETCH_HEAD", env=env)
        root = Path(config.path)
        touched: list[str] = []
        for step in steps:
            relative = config.files[step.target].path
            path = (root / relative).resolve()
            if not path.is_relative_to(root.resolve()) or path.is_symlink():
                raise ActuatorError(f"{relative} is not a file of the repository")
            try:
                text = path.read_text(encoding="utf-8")
                path.write_text(
                    edit_yaml_scalar(text, step.params["key"], step.params["value"]),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise ActuatorError(f"{relative}: {type(exc).__name__}") from None
            if relative not in touched:
                touched.append(relative)
        lines = [
            f"- {step.target}: {step.params['key']} = {step.params['value']}" for step in steps
        ]
        message = (
            f"Incident {proposal.incident_id}: {proposal.title[:60]}\n\n"
            "Proposed by Paeon. Merging this is the approval; nothing was applied.\n\n"
            + "\n".join(lines)
        )
        self._git("add", "--", *touched, env=env)
        self._git("commit", "--quiet", "-m", message, env=env)
        self._git("push", "--quiet", "--force", config.remote, f"{branch}:{branch}", env=env)
        return Change(branch, self._pull_request(proposal, branch, message), tuple(touched))

    def _pull_request(self, proposal: Proposal, branch: str, message: str) -> str | None:
        github = self._config.github
        if github is None or self._http is None:
            return None
        title, _, body = message.partition("\n\n")
        body += f"\n\nWhy: {proposal.summary}" if proposal.summary else ""
        try:
            response = self._http.post(
                f"/repos/{github.repo}/pulls",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"title": title, "head": branch, "base": self._config.base, "body": body},
            )
        except httpx.HTTPError as exc:
            raise ActuatorError(f"the pull request was not opened: {type(exc).__name__}") from None
        if response.status_code >= 400:
            # The branch is pushed either way: say so, and why there is no pull request.
            raise ActuatorError(
                f"the branch {branch} is pushed, but the pull request was refused"
                f" (HTTP {response.status_code})"
            )
        url = response.json().get("html_url")
        return url if isinstance(url, str) and url.startswith("https://") else None

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
