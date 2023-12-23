import abc
import dataclasses
import enum
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels_redis.pubsub import RedisPubSubChannelLayer

from documents.data_models import ConsumableDocument
from documents.data_models import DocumentMetadataOverrides


class ProgressStatusOptions(str, enum.Enum):
    STARTED = "STARTED"
    WORKING = "WORKING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ProgressManager:
    def __init__(self, filename: str, task_id: Optional[str] = None) -> None:
        self.filename = filename
        self._channel: Optional[RedisPubSubChannelLayer] = None
        self.task_id = task_id

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self) -> None:
        """
        If not already opened, gets the default channel layer
        opened and ready to send messages
        """
        if self._channel is None:
            self._channel = get_channel_layer()

    def close(self) -> None:
        """
        If it was opened, flushes the channel layer
        """
        if self._channel is not None:
            async_to_sync(self._channel.flush)
            self._channel = None

    def send_msg(
        self,
        status: ProgressStatusOptions,
        message: str,
        current_progress: int,
        max_progress: int,
        task_id: Optional[str] = None,
    ) -> None:
        # Ensure the layer is open
        self.open()

        # Just for IDEs
        if TYPE_CHECKING:
            assert self._channel is not None

        # Construct and send the update
        async_to_sync(self._channel.group_send)(
            "status_updates",
            {
                "type": "status_update",
                "data": {
                    "filename": self.filename,
                    "task_id": task_id or self.task_id,
                    "current_progress": current_progress,
                    "max_progress": max_progress,
                    "status": status,
                    "message": message,
                },
            },
        )


class PluginStatusCode(enum.Enum):
    # Continue to next plugin (if any)
    CONTINUE = enum.auto()
    # Exit the consume task (probably because a new task was created)
    EXIT_TASk = enum.auto()


@dataclasses.dataclass
class PluginStatus:
    code: PluginStatusCode
    message: Optional[str] = None


CONTINUE_STATUS = PluginStatus(PluginStatusCode.CONTINUE)


class ConsumeTaskPlugin(abc.ABC):
    def __init__(
        self,
        input_doc: ConsumableDocument,
        metadata: DocumentMetadataOverrides,
        status: ProgressManager,
        base_tmp_dir: Path,
    ) -> None:
        super().__init__()
        self.input_doc = input_doc
        self.metadata = metadata
        self.base_tmp_dir = base_tmp_dir
        self.status_mgr = status

    @abc.abstractproperty
    def able_to_run(self) -> bool:
        """
        Return True if the conditions are met for the plugin to run, False otherwise

        If False, setup(), handle() and cleanup() will not be called
        """

    @abc.abstractproperty
    def status(self) -> PluginStatus:
        """
        Allows the plugin to affect the progression of the consume task.  If
        a status with a code to exit the task is returned, the task will
        return with the given message string, not processing further plugins
        """
        # TODO: Another option is a special Exception, ie ExitConsumeTaskError which
        # includes the reason as the message and is caught by the task

    @abc.abstractmethod
    def setup(self) -> None:
        """
        Allows the plugin to preform any additional setup it may need, such as creating
        a temporary directory, copying a file somewhere, etc.

        Executed before handle()

        In general, this should be the "light" work, not the bulk of processing
        """

    @abc.abstractmethod
    def handle(self) -> None:
        """
        The bulk of plugin processing, this does whatever action the plugin is for.

        Executed after setup() and before cleanup()
        """

    @abc.abstractmethod
    def cleanup(self) -> None:
        """
        Allows the plugin to execute any cleanup it may require

        Executed after handle(), even in the case of error
        """
