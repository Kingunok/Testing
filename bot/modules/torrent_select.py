#!/usr/bin/env python3
import asyncio
import os
from typing import List, Tuple

import aiofiles
import aiohttp
import pyrogram
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.filters import regex, command, custom
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import bot.helper.telegram_helper.bot_commands as BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import sendMessage, sendStatusMessage, deleteMessage
from bot.helper.ext_utils.bot_utils import get_download_by_gid, MirrorStatus, bt_selection_buttons, sync_to_async

async def select(client: pyrogram.Client, message: Message):
    user_id = message.from_user.id
    cmd_data = message.text.split('_', maxsplit=1)
    gid = None
    if len(cmd_data) > 1:
        gid = cmd_data[1].split('@', maxsplit=1)[0].strip()
    elif message.reply_to_message:
        reply_message = message.reply_to_message
        async with download_dict_lock:
            download_info = download_dict.get(reply_message.message_id, None)
            if download_info:
                gid = download_info.get('gid')
    else:
        await sendMessage(message, "Invalid usage. Reply to a task or use /btselect <gid>")
        return

    if not gid:
        await sendMessage(message, "Task not found.")
        return

    dl = await get_download_by_gid(gid)
    if not dl:
        await sendMessage(message, "Task not found.")
        return

    if user_id not in (dl.message.from_user.id, OWNER_ID) and (user_id not in user_data or not user_data[user_id].get('is_sudo')):
        await sendMessage(message, "This task is not for you!")
        return

    if dl.status not in (MirrorStatus.STATUS_DOWNLOADING, MirrorStatus.STATUS_PAUSED, MirrorStatus.STATUS_QUEUED):
        await sendMessage(message, 'Task should be in download or pause (incase message deleted by wrong) or queued (status incase you used torrent file)!')
        return

    if dl.name.startswith('[METADATA]'):
        await sendMessage(message, 'Try after downloading metadata finished!')
        return

    try:
        if dl.is_qbit:
            id_ = dl.hash
            client_ = dl.client
            if not dl.queued:
                await sync_to_async(client_.torrents_pause, torrent_hashes=[id_])
        else:
            id_ = dl.gid
            if not dl.queued:
                await sync_to_async(aria2.client.force_pause, id_)
        dl.listener.select = True
    except Exception as e:
        await sendMessage(message, "This is not a bittorrent task!")
        return

    buttons = bt_selection_buttons(id_)
    msg = "Your download paused. Choose files then press Done Selecting button to resume downloading."
    await sendMessage(message, msg, buttons)


async def get_confirm(client: pyrogram.Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data.split()
    message = query.message
    dl = await get_download_by_gid(data[2])

    if not dl:
        await query.answer("This task has been cancelled!", show_alert=True)
        await deleteMessage(message)
        return

    if hasattr(dl, 'listener'):
        listener = dl.listener
    else:
        await query.answer("Not in download state anymore! Keep this message to resume the seed if seed enabled!", show_alert=True)
        return

    if user_id != listener.message.from_user.id and not await CustomFilters.sudo(client, query):
        await query.answer("This task is not for you!", show_alert=True)
        return

    if data[1] == "pin":
        await query.answer(data[3], show_alert=True)
    elif data[1] == "done":
        await query.answer()
        id_ = data[3]
        if len(id_) > 20:
            client_ = dl.client
            tor_info = (await sync_to_async(client_.torrents_info, torrent_hash=[id_]))[0]
            path = tor_info.content_path.rsplit('/', 1)[0]
            res = await sync_to_async(client_.torrents_files, torrent_hash=[id_])
            for f in res:
                if f.priority == 0:
                    f_paths = [os.path.join(path, f.name), os.path.join(path, f.name + '.!qB')]
                    for f_path in f_paths:
                        if await aiofiles.os.path.exists(f_path):
                            try:
                                await aiofiles.os.remove(f_path)
                            except Exception:
                                pass
            if not dl.queued:
                await sync_to_async(client_.torrents_resume, torrent_hashes=[id_])
        else:
            res = await sync_to_async(aria2.client.get_files, id_)
            for f in res:
                if not f['selected'] and await aiofiles.os.path.exists(f['path']):
                    try:
                        await aiofiles.os.remove(f['path'])
                    except Exception:
                        pass
            if not dl.queued:
                try:
                    await sync_to_async(aria2.client.unpause, id_)
                except Exception as e:
                    LOGGER.error(f"{e} Error in resume, this mostly happens after abuse aria2. Try to use select cmd again!")
        await sendStatusMessage(message)
        await deleteMessage(message)
    elif data[1] == "rm":
        await query.answer()
        try:
            await dl.download().cancel_download()
        except FloodWait as e:
            await asyncio.sleep(e.x)
        await deleteMessage(message)


bot.add_handler(MessageHandler(select, filters=regex(f"^/{BotCommands.BtSelectCommand}(_\w+)?") & custom.authorized & ~custom.blacklisted))
bot.add_handler(CallbackQueryHandler(get_confirm, filters=regex("^btsel")))
