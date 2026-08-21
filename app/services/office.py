from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.models import Switch

STACK_ROLE_FLOOR = "floor"
STACK_ROLE_AUX = "aux"
STACK_ROLE_CORE = "core"

ROLE_SORT = {
    STACK_ROLE_FLOOR: 0,
    STACK_ROLE_AUX: 1,
    STACK_ROLE_CORE: 2,
    "": 9,
}

ROOM_SORT = {
    "Level 27 Main Comms Room": 0,
    "L27 MCR": 0,
    "Level 26 IDF": 1,
    "L26 IDF": 1,
    "Level 21 IDF": 2,
    "L21 IDF": 2,
}

FLOOR_STACK_SORT = {
    "Level 27 Floor Stack": 0,
    "Level 26 Floor Stack": 1,
    "Level 21 Floor Stack": 2,
}


def office_slug(location: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (location or "").lower()).strip("-")
    return slug or "office"


def switch_sort_key(switch: Switch) -> tuple:
    return (
        (switch.location or "").lower(),
        ROOM_SORT.get(switch.room or "", 50),
        switch.room or "",
        ROLE_SORT.get(switch.stack_role or "", 9),
        FLOOR_STACK_SORT.get(switch.stack_name or "", 50),
        switch.stack_name or "",
        switch.rack_order or 0,
        switch.member_number or 0,
        switch.name,
    )


@dataclass
class StackView:
    name: str
    role: str
    room: str
    members: list[Switch]


@dataclass
class RoomView:
    name: str
    is_mcr: bool
    stacks: list[StackView]

    @property
    def has_non_floor(self) -> bool:
        return any(stack.role != STACK_ROLE_FLOOR for stack in self.stacks)


@dataclass
class OfficeGroup:
    location: str
    switches: list[Switch]


@dataclass
class OfficeView:
    name: str
    slug: str
    stacked: bool
    floor_stacks: list[StackView]
    mcr: RoomView | None
    other_rooms: list[RoomView]
    unstacked: list[Switch]


def _stack_from_members(members: list[Switch]) -> StackView:
    members = sorted(members, key=lambda s: (s.rack_order or 0, s.member_number or 0, s.name))
    first = members[0]
    return StackView(
        name=first.stack_name,
        role=first.stack_role,
        room=first.room,
        members=members,
    )


def _is_mcr(room_name: str, stacks: list[StackView]) -> bool:
    label = room_name.upper()
    if "MCR" in label or "MAIN COMMS" in label:
        return True
    return len({st.role for st in stacks if st.role}) > 1


def build_office_view(name: str, switches: list[Switch]) -> OfficeView:
    stacked = [s for s in switches if (s.stack_name or "").strip()]
    unstacked = sorted(
        [s for s in switches if not (s.stack_name or "").strip()],
        key=lambda s: s.name,
    )

    by_key: dict[tuple[str, str, str], list[Switch]] = defaultdict(list)
    for switch in stacked:
        by_key[(switch.room or "", switch.stack_name, switch.stack_role or "")].append(switch)
    stacks = [_stack_from_members(members) for members in by_key.values()]

    floor_stacks = [st for st in stacks if st.role == STACK_ROLE_FLOOR]
    floor_stacks.sort(key=lambda st: (FLOOR_STACK_SORT.get(st.name, 50), st.name))

    by_room: dict[str, list[StackView]] = defaultdict(list)
    for stack in stacks:
        by_room[stack.room or "Unspecified room"].append(stack)

    room_views: list[RoomView] = []
    for room_name, room_stacks in by_room.items():
        room_stacks.sort(key=lambda st: (ROLE_SORT.get(st.role, 9), st.name))
        room_views.append(
            RoomView(name=room_name, is_mcr=_is_mcr(room_name, room_stacks), stacks=room_stacks)
        )
    room_views.sort(key=lambda room: (ROOM_SORT.get(room.name, 50), room.name))

    mcr = next((room for room in room_views if room.is_mcr), None)
    if mcr is not None:
        mcr_stacks = [st for st in mcr.stacks if st.role != STACK_ROLE_FLOOR]
        mcr_stacks.sort(key=lambda st: (ROLE_SORT.get(st.role, 9), st.name))
        mcr = RoomView(name=mcr.name, is_mcr=True, stacks=mcr_stacks) if mcr_stacks else None
    other_rooms = [room for room in room_views if not room.is_mcr]

    return OfficeView(
        name=name,
        slug=office_slug(name),
        stacked=bool(stacked),
        floor_stacks=floor_stacks,
        mcr=mcr,
        other_rooms=other_rooms,
        unstacked=unstacked,
    )


def build_office_views(switches: Iterable[Switch]) -> list[OfficeView]:
    by_location: dict[str, list[Switch]] = defaultdict(list)
    for switch in switches:
        by_location[(switch.location or "").strip() or "Unspecified office"].append(switch)
    views = [build_office_view(name, items) for name, items in by_location.items()]
    views.sort(key=lambda view: view.name.lower())
    return views


def find_office(views: list[OfficeView], slug: str) -> OfficeView | None:
    wanted = (slug or "").strip().lower()
    for view in views:
        if view.slug == wanted:
            return view
    return None


def switches_grouped_by_office(switches: Iterable[Switch]) -> list[OfficeGroup]:
    ordered = sorted(switches, key=switch_sort_key)
    groups: list[OfficeGroup] = []
    for switch in ordered:
        location = (switch.location or "").strip() or "Unspecified office"
        if not groups or groups[-1].location != location:
            groups.append(OfficeGroup(location=location, switches=[]))
        groups[-1].switches.append(switch)
    return groups
