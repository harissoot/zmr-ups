from asyncio import gather
from json import loads
from secrets import token_urlsafe

from bot import (
    task_dict,
    task_dict_lock,
    queue_dict_lock,
    non_queued_dl,
    LOGGER,
    pkg_info
)
from bot.helper.ext_utils.bot_utils import cmd_exec
from bot.helper.ext_utils.status_utils import get_readable_file_size
from bot.helper.ext_utils.task_manager import (
    check_running_tasks,
    limit_checker,
    stop_duplicate_check
)
from bot.helper.task_utils.rclone_utils.transfer import RcloneTransferHelper
from bot.helper.task_utils.status_utils.queue_status import QueueStatus
from bot.helper.task_utils.status_utils.rclone_status import RcloneStatus
from bot.helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    sendMessage,
    sendStatusMessage
)


async def add_rclone_download(listener, path):
    if listener.link.startswith("mrcc:"):
        listener.link = listener.link.split(
            "mrcc:",
            1
        )[1]
        config_path = f"rclone/{listener.userId}.conf"
    else:
        config_path = "rclone.conf"

    (
        remote,
        listener.link
    ) = listener.link.split(":", 1)
    listener.link = listener.link.strip("/")

    cmd1 = [
        pkg_info["pkgs"][3],
        "lsjson",
        "--fast-list",
        "--stat",
        "--no-mimetype",
        "--no-modtime",
        "--config",
        config_path,
        f"{remote}:{listener.link}",
    ]
    cmd2 = [
        pkg_info["pkgs"][3],
        "size",
        "--fast-list",
        "--json",
        "--config",
        config_path,
        f"{remote}:{listener.link}",
    ]
    res1, res2 = await gather(
        cmd_exec(cmd1),
        cmd_exec(cmd2)
    )
    if res1[2] != res2[2] != 0:
        if res1[2] != -9:
            err = (
                res1[1]
                or res2[1]
                or "Use <code>/shell cat rlog.txt</code> to see more information"
            )
            msg = f"Error: While getting rclone stat/size. Path: {remote}:{listener.link}. Stderr: {err[:4000]}"
            await listener.onDownloadError(msg)
        return
    try:
        rstat = loads(res1[0])
        rsize = loads(res2[0])
    except Exception as err:
        if not str(err):
            err = "Use <code>/shell cat rlog.txt</code> to see more information"
        await listener.onDownloadError(f"RcloneDownload JsonLoad: {err}")
        return
    if rstat["IsDir"]:
        if not listener.name:
            listener.name = (
                listener.link.rsplit("/", 1)[-1]
                if listener.link
                else remote
            )
        path += listener.name
    else:
        listener.name = listener.link.rsplit(
            "/",
            1
        )[-1]
    listener.size = rsize["bytes"]
    gid = token_urlsafe(12)

    (
        msg,
        button
    ) = await stop_duplicate_check(listener)
    if msg:
        await listener.onDownloadError(
            msg,
            button
        )
        return
    if limit_exceeded := await limit_checker(listener, isRclone=True):
        LOGGER.info(f"Rclone Limit Exceeded: {listener.name} | {get_readable_file_size(listener.size)}")
        rmsg = await sendMessage(
            listener.message,
            limit_exceeded
        )
        await delete_links(listener.message)
        await auto_delete_message(
            listener.message,
            rmsg
        )
        return

    (
        add_to_queue,
        event
    ) = await check_running_tasks(listener)
    if add_to_queue:
        LOGGER.info(f"Added to Queue/Download: {listener.name}")
        async with task_dict_lock:
            task_dict[listener.mid] = QueueStatus(
                listener,
                gid,
                "dl"
            )
        await listener.onDownloadStart()
        if listener.multi <= 1:
            await sendStatusMessage(listener.message)
        await event.wait() # type: ignore
        if listener.isCancelled:
            return
        async with queue_dict_lock:
            non_queued_dl.add(listener.mid)

    RCTransfer = RcloneTransferHelper(listener)
    async with task_dict_lock:
        task_dict[listener.mid] = RcloneStatus(
            listener,
            RCTransfer,
            gid,
            "dl",
        )

    if add_to_queue:
        LOGGER.info(f"Start Queued Download with rclone: {listener.link}")
    else:
        await listener.onDownloadStart()
        if listener.multi <= 1:
            await sendStatusMessage(listener.message)
        LOGGER.info(f"Download with rclone: {listener.link}")

    await RCTransfer.download(
        remote,
        config_path,
        path
    )
