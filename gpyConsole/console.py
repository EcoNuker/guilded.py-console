import sys, types, threading, traceback, logging, inspect, asyncio

from .core import ConsoleCommand, ConsoleGroup
from . import errors
from .context import Context

import guilded
from guilded import ClientFeatures
from guilded.ext import commands
from guilded.ext.commands.core import Check
from guilded.ext.commands.bot import BotBase, HelpCommand, _default, StringView
from guilded.utils import MISSING

from typing import (
    Optional,
    List,
    Union,
    Any,
    Iterable,
    Callable,
    Type,
    TextIO,
)

logger = logging.getLogger("gpyConsole")


class DefaultConsoleHelpCommand:  # TODO
    def __init__(self):
        pass


# def _is_submodule(parent, child):
#     return parent == child or child.startswith(parent + ".")


class ConsoleMixin:
    """
    Mixin class to add console features.
    """

    def __init__(
        self,
        *args,
        console_help_command: Optional[HelpCommand] = _default,  # type: ignore
        # description: Optional[str] = None,
        **options: Any,
    ):
        super().__init__(**options)
        self.input: TextIO = options.get("input", sys.stdin)
        self.out: TextIO = options.get("out", sys.stdout)

        self._console_commands = {}
        self._console_help_command = None

        if console_help_command is _default:
            self._console_help_command = DefaultConsoleHelpCommand()
        else:
            self._console_help_command = console_help_command

        self._console_listeners = {
            "on_console_message": self.on_console_message,
            "on_console_command_error": self.on_console_command_error,
        }

        self._extend_listeners()

    def _extend_listeners(self):
        """
        Extend or modify the original listeners.
        """
        original_listeners = getattr(self, "_listeners", {})

        combined_listeners = {**original_listeners, **self._console_listeners}

        self._listeners = combined_listeners

    @property
    def console_commands(self):
        return list(self._console_commands.values())

    @property
    def _console_commands_by_alias(self):
        aliases = {}
        for command in self._console_commands:
            aliases = {**{alias: command for alias in command.aliases}, **aliases}
        return aliases

    @property
    def all_console_commands(self):
        return {**self._console_commands, **self._console_commands_by_alias}

    def add_console_command(self, command: ConsoleCommand):
        """Add a :class:`.ConsoleCommand` to the internal list of commands.

        Parameters
        -----------
        command: :class:`.ConsoleCommand`
            The command to register.

        Raises
        -------
        ConsoleCommandRegistrationError
            This command has a duplicate name or alias to one that is already
            registered.
        """
        if command.name in self._console_commands.keys():
            raise errors.ConsoleCommandRegistrationError(
                f"A console command with the name {command.name} is already registered."
            )
        elif command.name in self._console_commands_by_alias.keys():
            raise errors.ConsoleCommandRegistrationError(
                f"A console command with the alias {command.name} is already registered."
            )
        self._console_commands[command.name] = command

    def remove_console_command(self, name: str) -> Optional[ConsoleCommand]:
        """Remove a :class:`.ConsoleCommand` from the internal list of commands.

        This could also be used as a way to remove aliases.

        Parameters
        -----------
        name: :class:`str`
            The name of the command to remove.

        Returns
        --------
        Optional[:class:`.ConsoleCommand`]
            The command that was removed. If the name is not valid then
            ``None`` is returned instead.
        """
        command = self._console_commands.pop(
            name, self._console_commands_by_alias.get(name)
        )

        # does not exist
        if command is None:
            return None

        if name in command.aliases:
            # remove only this alias
            command.aliases.remove(name)
            return command

        return command

    def get_console_command(self, name: str) -> Optional[ConsoleCommand]:
        """Get a :class:`.ConsoleCommand` from the internal list of commands.

        This could also be used as a way to get aliases.

        The name could be fully qualified (e.g. ``'foo bar'``) will get
        the subcommand ``bar`` of the group command ``foo``. If a
        subcommand is not found then ``None`` is returned just as usual.

        Parameters
        -----------
        name: :class:`str`
            The name of the command to get.

        Returns
        --------
        Optional[:class:`.ConsoleCommand`]
            The command that was requested. If not found, returns ``None``.
        """
        # fast path, no space in name.
        if " " not in name:
            return self.all_console_commands.get(name)

        names = name.split()
        if not names:
            return None
        obj = self.all_console_commands.get(names[0])
        if not isinstance(obj, ConsoleGroup):
            return obj

        for name in names[1:]:
            try:
                obj = obj.all_commands[name]  # type: ignore
            except (AttributeError, KeyError):
                return None

        return obj

    def console_command(
        self,
        name: Optional[str] = None,
        cls: Type = ConsoleCommand,
        **kwargs: Any,
    ):
        def decorator(coro):
            if isinstance(coro, ConsoleCommand):
                raise TypeError("Function is already a console command.")
            kwargs["name"] = kwargs.get("name", name)
            command = cls(coro, **kwargs)
            self.add_console_command(command)
            return command

        return decorator

    def console_group(
        self,
        name: Optional[str] = None,
        cls: Type = ConsoleGroup,
        **kwargs: Any,
    ):
        def decorator(coro):
            if isinstance(coro, ConsoleGroup):
                raise TypeError("Function is already a group.")
            kwargs["name"] = kwargs.get("name", name)
            command = cls(coro, **kwargs)
            self.add_console_command(command)
            return command

        return decorator

    async def on_console_command_error(self, context: Context, exception):
        """|coro|

        The default console command error handler provided by the bot.

        By default this prints to :data:`sys.stderr` however it could be
        overridden to have a different implementation.

        This only fires if you do not specify any listeners for console command error.
        """
        # TODO: implement this, for now it'll always fire
        # if self.extra_events.get("on_console_command_error", None):
        #     return

        # command = context.command
        # if command and command.has_error_handler():
        #    return

        cog = context.cog
        if cog and cog.has_error_handler():
            return

        print(f"Ignoring exception in command {context.command}:", file=sys.stderr)
        traceback.print_exception(
            type(exception), exception, exception.__traceback__, file=sys.stderr
        )

    async def get_console_context(self, message: str, /, *, cls=Context) -> Context:
        view = StringView(message)
        ctx = cls(view=view, bot=self, message=message)

        return ctx

    async def console_invoke(self, ctx: Context) -> None:
        if ctx.command is not None:
            super().dispatch("console_command", ctx)
            try:
                if await self.can_run(ctx, call_once=True):
                    await ctx.command.invoke(ctx)
                else:
                    raise errors.ConsoleCheckFailure(
                        "The global check once functions failed."
                    )
            except errors.ConsoleCommandError as exc:
                super().dispatch("console_command_error", ctx, exc)
                # await ctx.command.dispatch_error(ctx, exc)
            else:
                super().dispatch("console_command_completion", ctx)
        elif ctx.invoked_with:
            exc = errors.ConsoleCommandNotFound(
                f'Console command "{ctx.invoked_with}" is not found'
            )
            super().dispatch("console_command_error", ctx, exc)

    async def process_console_commands(self, message: str):
        """|coro|

        This function processes the commands that have been registered to the
        bot and other groups. Without this coroutine, no commands will be
        triggered.

        By default, this coroutine is called inside the :func:`.on_console_message` event.
        If you choose to override the :func:`.on_console_message` event, then you should
        invoke this coroutine as well.

        This is built using other low level tools, and is equivalent to a call
        to :meth:`.get_context` followed by a call to :meth:`.invoke`.

        Parameters
        -----------
        message: :class:`str`
            The message to process commands for.
        """
        ctx = await self.get_console_context(message)
        await self.console_invoke(ctx)

    async def on_console_message(self, event: str):
        """|coro|

        The default handler for :func:`~.on_console_message` provided by the bot.

        If you are overriding this, remember to call :meth:`.process_commands`
        or all commands will be ignored.
        """
        await self.process_console_commands(event)

    # help command

    @property
    def help_command(self) -> Optional[HelpCommand]:
        return self._help_command

    @help_command.setter
    def help_command(self, value: Optional[HelpCommand]) -> None:
        if value is not None:
            if not isinstance(value, HelpCommand):
                raise TypeError("help_command must be a subclass of HelpCommand")
            if self._help_command is not None:
                self._help_command._remove_from_bot(self)
            self._help_command = value
            value._add_to_bot(self)
        elif self._help_command is not None:
            self._help_command._remove_from_bot(self)
            self._help_command = None
        else:
            self._help_command = None

    def _on_console(self):
        """
        Console starts listening for inputs.
        This is a blocking call. To avoid the bot being stopped this has to run in an executor (New Thread)
        :return:
        """
        logger.info("Console is ready and is listening for commands\n")
        while True:
            try:
                console_in = self.input.read().strip()
                if len(console_in) == 0:
                    continue
                super().dispatch("console_message", console_in)
            except Exception:
                traceback.print_exc()

    def start_console(self):
        """
        Abstracts Thread initialization away from user.
        :return:
        """
        thread = threading.Thread(None, self._on_console, daemon=True)
        thread.start()


class ConsoleClient(guilded.Client, ConsoleMixin):
    def __init__(
        self,
        *,
        internal_server_id: Optional[str] = None,
        max_messages: Optional[int] = MISSING,
        features: Optional[ClientFeatures] = None,
        **options,
    ):
        guilded.Client.__init__(
            self,
            internal_server_id=internal_server_id,
            max_messages=max_messages,
            features=features,
            **options,
        )
        ConsoleMixin.__init__(self, **options)


class ConsoleBot(commands.Bot, ConsoleMixin):
    def __init__(
        self,
        command_prefix: Union[
            Callable[[BotBase, guilded.Message], Union[Iterable[str], str]],
            Iterable[str],
            str,
        ],
        *,
        help_command: Optional[HelpCommand] = _default,  # type: ignore
        description: Optional[str] = None,
        owner_id: Optional[str] = None,
        owner_ids: Optional[List[str]] = None,
        **options: Any,
    ):
        # Initialize the BotBase class
        commands.Bot.__init__(
            self,
            command_prefix,
            help_command=help_command,
            description=description,
            owner_id=owner_id,
            owner_ids=owner_ids,
            **options,
        )
        ConsoleMixin.__init__(self, **options)
