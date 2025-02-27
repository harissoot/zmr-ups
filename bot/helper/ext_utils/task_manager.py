from asyncio import (
    Event,
    sleep
)

from bot import (
    config_dict,
    queued_dl,
    queued_up,
    non_queued_up,
    non_queued_dl,
    queue_dict_lock,
    LOGGER,
)
from bot.helper.ext_utils.bot_utils import (
    sync_to_async,
    get_telegraph_list,
)
from bot.helper.ext_utils.files_utils import (
    check_storage_threshold,
    get_base_name
)
from bot.helper.ext_utils.links_utils import is_gdrive_id
from bot.helper.ext_utils.status_utils import (
    getSpecificTasks,
    get_readable_file_size
)
from bot.helper.task_utils.gdrive_utils.search import gdSearch
from bot.helper.telegram_helper.message_utils import isAdmin


async def stop_duplicate_check(listener):
    if (
        isinstance(
            listener.upDest,
            int
        )
        or listener.isLeech
        or listener.select
        or not is_gdrive_id(listener.upDest)
        or (
            listener.upDest.startswith("mtp:")
            and listener.stopDuplicate
        )
        or not listener.stopDuplicate
        or listener.sameDir
    ):
        return (
            False,
            None
        )

    name = listener.name
    LOGGER.info(f"Checking File/Folder if already in Drive: {name}")

    if listener.compress:
        name = f"{name}.zip"
    elif listener.extract:
        try:
            name = get_base_name(name)
        except:
            name = None

    if name is not None:
        (
            telegraph_content,
            contents_no
        ) = await sync_to_async(
            gdSearch(
                stopDup=True,
                noMulti=listener.isClone
            ).drive_list,
            name,
            listener.upDest,
            listener.userId,
        )
        if telegraph_content:
            msg = f"File/Folder is already available in Drive.\nHere are {contents_no} list results:"
            button = await get_telegraph_list(telegraph_content)
            return msg, button

    return (
        False,
        None
    )


async def check_running_tasks(listener, state="dl"):
    all_limit = config_dict["QUEUE_ALL"]
    state_limit = (
        config_dict["QUEUE_DOWNLOAD"]
        if state == "dl"
        else config_dict["QUEUE_UPLOAD"]
    )
    event = None
    is_over_limit = False
    async with queue_dict_lock:
        if (
            state == "up"
            and listener.mid in non_queued_dl
        ):
            non_queued_dl.remove(listener.mid)
        if (
            (all_limit or state_limit)
            and not listener.forceRun
            and not (
                listener.forceUpload
                and state == "up"
            )
            and not (
                listener.forceDownload
                and state == "dl"
            )
        ):
            dl_count = len(non_queued_dl)
            up_count = len(non_queued_up)
            t_count = (
                dl_count
                if state == "dl"
                else up_count
            )
            is_over_limit = (
                all_limit
                and dl_count + up_count >= all_limit
                and (
                    not state_limit
                    or t_count >= state_limit
                )
            ) or (state_limit and t_count >= state_limit)
            if is_over_limit:
                event = Event()
                if state == "dl":
                    queued_dl[listener.mid] = event
                else:
                    queued_up[listener.mid] = event
        if not is_over_limit:
            if state == "up":
                non_queued_up.add(listener.mid)
            else:
                non_queued_dl.add(listener.mid)

    return (
        is_over_limit,
        event
    )


async def start_dl_from_queued(mid: int):
    queued_dl[mid].set()
    del queued_dl[mid]
    await sleep(0.7)


async def start_up_from_queued(mid: int):
    queued_up[mid].set()
    del queued_up[mid]
    await sleep(0.7)


async def start_from_queued():
    if all_limit := config_dict["QUEUE_ALL"]:
        dl_limit = config_dict["QUEUE_DOWNLOAD"]
        up_limit = config_dict["QUEUE_UPLOAD"]
        async with queue_dict_lock:
            dl = len(non_queued_dl)
            up = len(non_queued_up)
            all_ = dl + up
            if all_ < all_limit:
                f_tasks = all_limit - all_
                if queued_up and (
                    not up_limit
                    or up < up_limit
                ):
                    for (
                        index,
                        mid
                    ) in enumerate(
                        list(queued_up.keys()),
                        start=1
                    ):
                        f_tasks = all_limit - all_
                        await start_up_from_queued(mid)
                        f_tasks -= 1
                        if f_tasks == 0 or (
                            up_limit
                            and index >= up_limit - up
                        ):
                            break
                if queued_dl and (
                    not dl_limit
                    or dl < dl_limit
                ) and f_tasks != 0:
                    for index, mid in enumerate(
                        list(queued_dl.keys()),
                        start=1
                    ):
                        await start_dl_from_queued(mid)
                        if (
                            dl_limit
                            and index >= dl_limit - dl
                        ) or index == f_tasks:
                            break
        return

    if up_limit := config_dict["QUEUE_UPLOAD"]:
        async with queue_dict_lock:
            up = len(non_queued_up)
            if (
                queued_up and
                up < up_limit
            ):
                f_tasks = up_limit - up
                for (
                    index,
                    mid
                ) in enumerate(
                    list(queued_up.keys()),
                    start=1
                ):
                    await start_up_from_queued(mid)
                    if index == f_tasks:
                        break
    else:
        async with queue_dict_lock:
            if queued_up:
                for mid in list(queued_up.keys()):
                    await start_up_from_queued(mid)

    if dl_limit := config_dict["QUEUE_DOWNLOAD"]:
        async with queue_dict_lock:
            dl = len(non_queued_dl)
            if (
                queued_dl and
                dl < dl_limit
            ):
                f_tasks = dl_limit - dl
                for (
                    index,
                    mid
                ) in enumerate(
                    list(queued_dl.keys()),
                    start=1
                ):
                    await start_dl_from_queued(mid)
                    if index == f_tasks:
                        break
    else:
        async with queue_dict_lock:
            if queued_dl:
                for mid in list(queued_dl.keys()):
                    await start_dl_from_queued(mid)


async def check_user_tasks(user_id, maxtask):
    all_tasks = await sync_to_async(
        getSpecificTasks,
        "All",
        user_id
    )
    return len(all_tasks) >= maxtask


async def list_checker(listener):
    try:
        if await isAdmin(listener.message):
            return
    except Exception as e:
        LOGGER.error(f"Error while checking if the user is Admin: {e}")
    if listener.is_playlist:
        if PLAYLIST_LIMIT := config_dict["PLAYLIST_LIMIT"]:
            if listener.playlist_count > PLAYLIST_LIMIT:
                return f"Playlist limit is {PLAYLIST_LIMIT}\n⚠ Your Playlist has {listener.playlist_count} items."


async def limit_checker(
        listener,
        isTorrent=False,
        isMega=False,
        isDriveLink=False,
        isRclone=False,
    ):
    try:
        if await isAdmin(listener.message):
            return
    except Exception as e:
        LOGGER.error(f"Error while checking if the user is Admin: {e}")

    GB = 1024 ** 3
    limit_exceeded = ""

    def check_limit(limit, size, limit_type):
        limit_bytes = limit * GB
        if size > limit_bytes:
            return f"{limit_type} limit is {get_readable_file_size(limit_bytes)}"
        return ""

    limit_configs = [
        (
            listener.isYtDlp,
            "YTDLP_LIMIT",
            "Yt-Dlp"
        ),
        (
            listener.is_playlist,
            "PLAYLIST_LIMIT",
            "Yt-Dlp Playlist",
            "playlist_count"
        ),
        (
            listener.isClone,
            "CLONE_LIMIT",
            "Clone"
        ),
        (
            isRclone,
            "RCLONE_LIMIT",
            "Rclone"
        ),
        (
            isMega,
            "MEGA_LIMIT",
            "Mega"
        ),
        (
            isDriveLink,
            "GDRIVE_LIMIT",
            "Google drive"
        ),
        (
            isTorrent or listener.isTorrent,
            "TORRENT_LIMIT",
            "Torrent"
        ),
        (
            True,
            "DIRECT_LIMIT",
            "Direct"
        )
    ]

    for (
        condition,
        limit_key,
        limit_type,
        *optional
    ) in limit_configs:
        if condition:
            limit = config_dict.get(limit_key)
            if limit:
                size = getattr(
                    listener,
                    optional[0],
                    listener.size
                ) if optional else listener.size
                limit_exceeded = check_limit(
                    limit,
                    size,
                    limit_type
                )
                if limit_exceeded:
                    break

    if (
        not limit_exceeded
        and listener.isLeech
    ):
        limit = config_dict.get("LEECH_LIMIT")
        if limit:
            limit_exceeded = check_limit(
                limit,
                listener.size,
                "Leech"
            )

    if (
        not limit_exceeded
        and config_dict.get("STORAGE_THRESHOLD")
        and not listener.isClone
    ):
        arch = any([
            listener.compress,
            listener.extract
        ])
        limit = config_dict["STORAGE_THRESHOLD"] * GB
        acpt = await sync_to_async(
            check_storage_threshold,
            listener.size,
            limit,
            arch
        )
        if not acpt:
            limit_exceeded = f"Don't have enough free space for your task.\nYou must leave {get_readable_file_size(limit)} free storage"

    if limit_exceeded:
        if listener.is_playlist:
            return limit_exceeded
        return f"{limit_exceeded}.\n⚠ Your task size is {get_readable_file_size(listener.size)}"


async def check_avg_speed(total_speed, count):
    if AVG_SPEED := config_dict["AVG_SPEED"]:
        avg_speed = AVG_SPEED * 1024**2
        task_avg_speed = total_speed / count
        if task_avg_speed < avg_speed:
            return(
                f"⚠ Minimum download speed must be above {get_readable_file_size(avg_speed)}ps."
                f"\nYour task's average download speed is {get_readable_file_size(task_avg_speed)}ps."
            )
