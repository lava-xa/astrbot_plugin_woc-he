from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp


@register(
    "astrbot_plugin_at_info",
    "lava",
    "监听白名单 QQ 群聊中的 @ 消息，并输出被 @ 用户的 QQ 号和昵称",
    "1.0.0",
)
class AtInfoPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

    def _get_config_value(self, key: str, default=None):
        if self.config and hasattr(self.config, "get"):
            return self.config.get(key, default)
        return default

    def _normalize_string_list(self, values: object) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = str(value).strip()
            if not item or item in seen:
                continue
            normalized.append(item)
            seen.add(item)
        return normalized

    def _get_whitelist_group_ids(self) -> set[str]:
        return set(self._normalize_string_list(self._get_config_value("whitelist_group_ids", [])))

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", None) or getattr(message_obj, "session_id", None)
        return str(group_id or "").strip()

    def _get_message_chain(self, event: AstrMessageEvent) -> list[Any]:
        message_obj = getattr(event, "message_obj", None)
        message = getattr(message_obj, "message", None)
        return list(message or [])

    def _get_component_text_value(self, component: Any, keys: tuple[str, ...]) -> str:
        for key in keys:
            value = getattr(component, key, None)
            if value is None and isinstance(component, dict):
                value = component.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _get_at_qq(self, component: Any) -> str:
        if not isinstance(component, Comp.At) and component.__class__.__name__.lower() != "at":
            return ""
        return self._get_component_text_value(component, ("qq", "user_id", "target", "id"))

    def _get_at_name(self, component: Any) -> str:
        return self._get_component_text_value(
            component,
            ("name", "nickname", "display", "card", "text"),
        )

    def _iter_raw_at_data(self, raw_message: Any) -> list[dict]:
        if isinstance(raw_message, dict):
            raw_segments = raw_message.get("message", [])
        else:
            raw_segments = getattr(raw_message, "message", [])

        if not isinstance(raw_segments, list):
            return []

        at_data: list[dict] = []
        for segment in raw_segments:
            if not isinstance(segment, dict) or segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            if isinstance(data, dict):
                at_data.append(data)
        return at_data

    def _get_raw_at_name_map(self, event: AstrMessageEvent) -> dict[str, str]:
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        name_map: dict[str, str] = {}

        for data in self._iter_raw_at_data(raw_message):
            qq = str(data.get("qq") or data.get("user_id") or "").strip()
            name = str(data.get("name") or data.get("nickname") or data.get("card") or "").strip()
            if qq and name and qq not in name_map:
                name_map[qq] = name

        return name_map

    async def _get_group_member_name(self, event: AstrMessageEvent, group_id: str, qq: str) -> str:
        bot = getattr(event, "bot", None)
        if not bot:
            return ""

        payload = {
            "group_id": int(group_id) if group_id.isdigit() else group_id,
            "user_id": int(qq) if qq.isdigit() else qq,
            "no_cache": False,
        }

        try:
            if hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                info = await bot.api.call_action("get_group_member_info", **payload)
            elif hasattr(bot, "call_action"):
                info = await bot.call_action("get_group_member_info", **payload)
            else:
                return ""
        except Exception as e:
            logger.warning(f"获取群成员信息失败 group_id={group_id}, qq={qq}: {e}")
            return ""

        if not isinstance(info, dict):
            return ""
        return str(info.get("card") or info.get("nickname") or "").strip()

    async def _collect_at_members(self, event: AstrMessageEvent, group_id: str) -> list[tuple[str, str]]:
        raw_name_map = self._get_raw_at_name_map(event)
        members: list[tuple[str, str]] = []
        seen: set[str] = set()

        for component in self._get_message_chain(event):
            qq = self._get_at_qq(component)
            if not qq or qq.lower() == "all" or qq in seen:
                continue

            name = self._get_at_name(component) or raw_name_map.get(qq, "")
            if not name:
                name = await self._get_group_member_name(event, group_id, qq)

            members.append((qq, name or "未知"))
            seen.add(qq)

        return members

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        group_id = self._get_group_id(event)
        if group_id not in self._get_whitelist_group_ids():
            return

        members = await self._collect_at_members(event, group_id)
        if not members:
            return

        lines = [f"QQ: {qq}\n昵称: {name}" for qq, name in members]
        yield event.plain_result("\n\n".join(lines))

    async def terminate(self):
        pass
