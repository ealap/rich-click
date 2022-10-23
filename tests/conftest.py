# flake8: noqa D*
import importlib
import json
from dataclasses import asdict
from importlib import reload
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, cast, Dict, Optional, Type, Union

import click
import pytest
from click.testing import CliRunner, Result
from typing_extensions import Protocol

import rich_click.rich_click as rc
from rich_click.rich_command import RichCommand
from rich_click.rich_group import RichGroup
from rich_click.rich_help_configuration import OptionHighlighter, RichHelpConfiguration


@pytest.fixture
def root_dir():
    return Path(__file__).parent


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tmpdir(root_dir: Path):
    return root_dir / "tmp"


@pytest.fixture
def expectations_dir(root_dir: Path):
    return root_dir / "expectations"


class AssertStr:
    def __call__(self, actual: str, expectation: Union[str, Path]):
        """Assert strings by normalizining line endings

        Args:
            actual: actual result
            expectation: expected result `str` or `Path` to load result
        """
        ...


@pytest.fixture
def assert_str(request: pytest.FixtureRequest, tmpdir: Path):
    def assertion(actual: str, expectation: Union[str, Path]):
        if isinstance(expectation, Path):
            if expectation.exists():
                expected = expectation.read_text()
            else:
                expected = ""
        else:
            expected = expectation
        normalized_expected = [line.strip() for line in expected.strip().splitlines() if line.strip()]
        normalized_actual = [line.strip() for line in actual.strip().splitlines() if line.strip()]

        try:
            assert normalized_expected == normalized_actual
        except Exception:
            tmpdir.mkdir(parents=True, exist_ok=True)
            tmppath = tmpdir / f"{request.node.name}.out"
            tmppath.write_text(actual.strip())
            raise

    return assertion


class AssertDicts(Protocol):
    def __call__(self, actual: Dict[str, Any], expectation: Union[Path, Dict[str, Any]]):
        """Assert two dictionaries by normalizing as json

        Args:
            actual: actual result
            expectation: expected result `Dict` or `Path` to load result
        """
        ...


@pytest.fixture
def assert_dicts(request: pytest.FixtureRequest, tmpdir: Path):
    def load_obj(s: str) -> Any:
        return json.loads(s)

    def dump_obj(obj: Any) -> str:
        return json.dumps(obj, indent=4)

    def roundtrip(obj):
        return load_obj(dump_obj(obj))

    def assertion(actual: Dict[str, Any], expectation: Union[Path, Dict[str, Any]]):
        if isinstance(expectation, Path):
            if expectation.exists():
                expected = load_obj(expectation.read_text())
            else:
                expected = {}
        else:
            expected = expectation

        # need to perform a roundtrip to convert to
        # supported json data types (i.e. tuple -> list, datetime -> str, etc...)
        actual = roundtrip(actual)
        try:
            assert actual == expected
        except Exception:
            tmpdir.mkdir(parents=True, exist_ok=True)
            tmppath = tmpdir / f"{request.node.name}.config.json"
            with tmppath.open("w") as stream:
                stream.write(dump_obj(actual))
            raise

    return assertion


@pytest.fixture(autouse=True)
def initialize_rich_click():
    """Initialize `rich_click` module."""
    # to isolate module-level configuration we
    # must reload the rich_click module between
    # each test
    reload(rc)
    rc.MAX_WIDTH = 80


class LoadCommandModule(Protocol):
    def __call__(self, namespace: str) -> ModuleType:
        """Dynamically loads a rich cli fixture.

        Args:
            namespace: Namespace of the rich cli module under test.
                Example: fixtures.arguments
        """
        ...


@pytest.fixture
def load_command():
    def load(namespace: str):
        # set fixed terminal width for all commands
        if namespace:
            # reload the cli module to reset state
            # for multiple tests of the same cli command
            module = importlib.import_module(namespace)
            reload(module)
            return module

    return load


class InvokeCli(Protocol):
    def __call__(self, cmd: click.BaseCommand, *args: str) -> Result:
        """Invoke click command.

        Small convenience fixture to allow invoking a click Command
        without standalone mode.

        Args:
            cmd: Click Command
        """
        ...


@pytest.fixture
def invoke():
    runner = CliRunner()

    def invoke(cmd, *args, **kwargs):
        result = runner.invoke(cmd, *args, **kwargs, standalone_mode=False)
        return result

    return invoke


class AssertRichFormat(Protocol):
    def __call__(
        self,
        cmd: Union[str, RichCommand, RichGroup],
        args: str,
        error: Optional[Type[Exception]],
        rich_config: Optional[Callable[[Any], Union[RichGroup, RichCommand]]],
    ):
        """Invokes the cli command and applies assertions against the results

        This command resolves the cli application from the fixtures directory dynamically
        to isolate module configuration state between tests. It will also assert that
        the configuration (input), stdout (output) are as expected.

        If an assertion fails. It will dump the output into a tmp directory under the test
        folder with the name of the the test. The idea is that you can then validate the
        output visually, and once satisfied, copy it into the expectations folder.

        NOTE: This could be made better by dumping Rich's render tree as a dictionary.
        Currently it only asserts the output from the string rendered by Rich Console.
        This means it will miss cases where assertion of styles is desired.

        Args:
            cmd: The name of the module under test, or a `RichCommand` or `RichGroup` object.
                If given a module name. This module must have a module-level
                `cli` attribute that resolves to a Rich Command or Group
            args: The arguments to invoke the command with
            error: Optional exception to assert
            rich_config: Optional rich_config function to be applied to the command
        """
        ...


@pytest.fixture
def assert_rich_format(
    request: pytest.FixtureRequest,
    expectations_dir: Path,
    invoke: InvokeCli,
    load_command,
    assert_dicts,
    assert_str,
):
    def config_to_dict(config: RichHelpConfiguration):
        config_dict = asdict(config)
        config_dict["highlighter"] = cast(OptionHighlighter, config.highlighter).highlights
        return config_dict

    def assertion(
        cmd: Union[str, Union[RichCommand, RichGroup]],
        args: str,
        error: Optional[Type[Exception]],
        rich_config: Optional[Callable[[Any], Union[RichGroup, RichCommand]]],
    ):
        if isinstance(cmd, str):
            command: Union[RichCommand, RichGroup] = load_command(f"fixtures.{cmd}").cli
        else:
            command = cmd

        if rich_config:
            result = invoke(rich_config(command), args)
        else:
            result = invoke(command, args)

        assert command.formatter is not None
        if error:
            assert isinstance(result.exception, error)
            actual = command.formatter.getvalue()
        else:
            actual = result.stdout

        expectation_output_path = expectations_dir / f"{request.node.name}.out"
        expectation_config_path = expectations_dir / f"{request.node.name}.config.json"
        assert_str(actual, expectation_output_path)
        assert_dicts(config_to_dict(command.formatter.config), expectation_config_path)

    return assertion
