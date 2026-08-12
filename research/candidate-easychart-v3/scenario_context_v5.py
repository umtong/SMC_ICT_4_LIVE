"""Context, structure clustering and interaction discovery for EasyChart v5."""
from __future__ import annotations

from domain import Candle, Side
from easychart_zones import ZoneSide
from contracts_v5 import ScenarioPath, ScenarioSetup, SetupState, StructureFamily, StructureZone


class ScenarioContextMixin:
    @staticmethod
    def _side_for_zone(zone: StructureZone) -> Side:
        return Side.LONG if zone.side is ZoneSide.SUPPORT else Side.SHORT
    @staticmethod
    def _touches(bar: Candle, zone: StructureZone) -> bool:
        return bar.low <= zone.upper and bar.high >= zone.lower
    def _projected_members(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[StructureZone, ...]:
        return tuple(self.structure.snapshot_for(member, time_ns) for member in setup.context_members)
    def _projected_bounds(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[tuple[StructureZone, ...], float, float]:
        members = self._projected_members(setup, time_ns)
        return members, min(item.lower for item in members), max(item.upper for item in members)
    def _cluster(self, zones: list[StructureZone]) -> list[tuple[StructureZone, ...]]:
        if not zones:
            return []
        ordered = sorted(zones, key=lambda item: (item.side.value, item.lower, item.upper, item.zone_id))
        output: list[list[StructureZone]] = []
        for zone in ordered:
            if not output:
                output.append([zone])
                continue
            current = output[-1]
            same_side = current[0].side is zone.side
            current_upper = max(item.upper for item in current)
            if same_side and zone.lower <= current_upper + self.tick_size:
                current.append(zone)
            else:
                output.append([zone])
        return [tuple(group) for group in output]
    @staticmethod
    def _family_priority(zone: StructureZone) -> int:
        return {
            StructureFamily.CHANNEL: 3,
            StructureFamily.TREND_LINE: 2,
            StructureFamily.HORIZONTAL: 1,
        }[zone.family]
    def _primary(self, members: tuple[StructureZone, ...]) -> StructureZone:
        return max(
            members,
            key=lambda item: (
                item.source_pivot_span,
                self._family_priority(item),
                item.strength_ratio,
                item.zone_id,
            ),
        )
    def _selected_clusters(
        self,
        bar: Candle,
        previous: Candle,
    ) -> list[tuple[StructureZone, tuple[StructureZone, ...], StructureZone | None]]:
        current = [
            zone
            for zone in self.structure.boundaries_at(bar.ts_close_ns)
            if zone.observed_time_ns < bar.ts_close_ns
            and zone.source_structure_id not in self._claimed_structures
            and self._touches(bar, zone)
        ]
        previous_by_source = {
            zone.source_structure_id: zone
            for zone in self.structure.boundaries_at(previous.ts_close_ns)
        }
        clusters = self._cluster(current)
        support = [group for group in clusters if group[0].side is ZoneSide.SUPPORT]
        resistance = [group for group in clusters if group[0].side is ZoneSide.RESISTANCE]
        selected: list[tuple[StructureZone, tuple[StructureZone, ...], StructureZone | None]] = []
        if support:
            group = min(support, key=lambda items: min(item.lower for item in items))
            primary = self._primary(group)
            selected.append((primary, group, previous_by_source.get(primary.source_structure_id)))
        if resistance:
            group = max(resistance, key=lambda items: max(item.upper for item in items))
            primary = self._primary(group)
            selected.append((primary, group, previous_by_source.get(primary.source_structure_id)))
        if len(selected) == 2:
            self._inc("decision_bar_touched_both_sides_unresolved")
            self._trace(
                "decision_bar_touched_both_sides_unresolved",
                bar.ts_close_ns,
                support_zone=selected[0][0].zone_id,
                resistance_zone=selected[1][0].zone_id,
            )
            return []
        return selected
    def _channel_target(
        self,
        context: StructureZone,
        side: Side,
        time_ns: int,
    ) -> tuple[StructureZone, float, str, float] | None:
        if context.family is not StructureFamily.CHANNEL:
            return None
        channel = self.structure.channel_for_boundary(context.source_structure_id)
        if channel is None:
            return None
        edge = "UPPER" if side is Side.LONG else "LOWER"
        target = self.structure.channel_edge_snapshot(channel, edge, time_ns)
        price = channel.upper_at(time_ns) if side is Side.LONG else channel.lower_at(time_ns)
        return target, price, channel.channel_id, channel.mid_at(time_ns)
    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> tuple[StructureZone, float, str | None, float | None] | None:
        if path in {ScenarioPath.REJECTION, ScenarioPath.ROTATION, ScenarioPath.BOUNCE}:
            channel = self._channel_target(context, side, bar.ts_close_ns)
            if channel is not None:
                zone, price, channel_id, mid = channel
                return zone, price, channel_id, mid
        target = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        if target is None:
            return None
        zone, price = target
        return zone, price, None, None
    def _create_setup(
        self,
        *,
        path: ScenarioPath,
        context: StructureZone,
        members: tuple[StructureZone, ...],
        bar: Candle,
        decision_index: int,
        state: SetupState,
    ) -> ScenarioSetup | None:
        side = (
            (Side.SHORT if context.side is ZoneSide.SUPPORT else Side.LONG)
            if path is ScenarioPath.ACCEPTANCE
            else self._side_for_zone(context)
        )
        structure_key = "|".join(sorted(member.source_structure_id for member in members))
        episode_id = f"{self.scale_name}:{structure_key}:{bar.ts_close_ns}"
        if any(member.source_structure_id in self._claimed_structures for member in members):
            self._inc("first_structure_interaction_already_claimed")
            return None
        if episode_id in self._claimed_episodes:
            self._inc("duplicate_structure_episode")
            return None
        channel_members = [member for member in members if member.family is StructureFamily.CHANNEL]
        target_context = (
            max(
                channel_members,
                key=lambda item: (item.source_pivot_span, item.strength_ratio, item.zone_id),
            )
            if channel_members and path in {ScenarioPath.REJECTION, ScenarioPath.ROTATION}
            else context
        )
        target = self._select_target(target_context, side, path, bar)
        if target is None:
            setup = ScenarioSetup(
                setup_id=f"{episode_id}:{path.value}",
                scale_name=self.scale_name,
                path=path,
                side=side,
                state=SetupState.NO_TARGET,
                context=context,
                context_members=members,
                observed_time_ns=context.observed_time_ns,
                interaction_time_ns=bar.ts_close_ns,
                interaction_index=decision_index,
                interaction_extreme=bar.low if side is Side.LONG else bar.high,
                target_zone=None,
                target_price=None,
            )
            self.setups.append(setup)
            self._inc("no_preexisting_target")
            self._trace("no_preexisting_target", bar.ts_close_ns, setup)
            for member in members:
                self._claimed_structures.add(member.source_structure_id)
            self._claimed_episodes.add(episode_id)
            return None
        target_zone, target_price, channel_id, midline = target
        origin = (
            self.structure.acceptance_origin(
                side,
                before_time_ns=bar.ts_close_ns,
                source_span=context.source_pivot_span,
            )
            if path is ScenarioPath.ACCEPTANCE
            else None
        )
        has_channel_context = any(member.family is StructureFamily.CHANNEL for member in members)
        if path is ScenarioPath.ACCEPTANCE and not has_channel_context and origin is None:
            setup = ScenarioSetup(
                setup_id=f"{episode_id}:{path.value}",
                scale_name=self.scale_name,
                path=path,
                side=side,
                state=SetupState.UNRESOLVED,
                context=context,
                context_members=members,
                observed_time_ns=max(member.observed_time_ns for member in members),
                interaction_time_ns=bar.ts_close_ns,
                interaction_index=decision_index,
                interaction_extreme=bar.low if side is Side.LONG else bar.high,
                target_zone=target_zone,
                target_price=target_price,
                acceptance_break_index=decision_index,
                terminal_reason="acceptance_no_causal_origin",
            )
            self.setups.append(setup)
            for member in members:
                self._claimed_structures.add(member.source_structure_id)
                self._audit(member)
            self._audit(target_zone)
            self._claimed_episodes.add(episode_id)
            self._inc("acceptance_no_causal_origin")
            self._trace("acceptance_no_causal_origin", bar.ts_close_ns, setup)
            return None
        setup = ScenarioSetup(
            setup_id=f"{episode_id}:{path.value}",
            scale_name=self.scale_name,
            path=path,
            side=side,
            state=state,
            context=context,
            context_members=members,
            observed_time_ns=max(member.observed_time_ns for member in members),
            interaction_time_ns=bar.ts_close_ns,
            interaction_index=decision_index,
            interaction_extreme=bar.low if side is Side.LONG else bar.high,
            target_zone=target_zone,
            target_price=target_price,
            confirmation_time_ns=(bar.ts_close_ns if state is SetupState.WAITING_DISPLACEMENT else None),
            acceptance_break_index=(decision_index if path is ScenarioPath.ACCEPTANCE else None),
            acceptance_origin=origin,
            channel_id=channel_id,
            midline_price_at_interaction=midline,
        )
        self.setups.append(setup)
        self._active[setup.setup_id] = setup
        for member in members:
            self._claimed_structures.add(member.source_structure_id)
            self._audit(member)
        self._audit(target_zone)
        self._claimed_episodes.add(episode_id)
        self._inc(f"setup_{path.value.lower()}_created")
        self._trace(
            f"setup_{path.value.lower()}_created",
            bar.ts_close_ns,
            setup,
            context_members=[member.kind.value for member in members],
            target_zone_id=target_zone.zone_id,
            target_price=target_price,
        )
        return setup
    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        for context, members, previous_zone in self._selected_clusters(bar, previous):
            side = self._side_for_zone(context)
            lower = min(item.lower for item in members)
            upper = max(item.upper for item in members)
            if side is Side.LONG:
                breached = bar.low < lower
                fully_inside = bar.close > upper
                outside_close = bar.close < lower
                previous_inside = previous_zone is None or previous.close >= previous_zone.lower
            else:
                breached = bar.high > upper
                fully_inside = bar.close < lower
                outside_close = bar.close > upper
                previous_inside = previous_zone is None or previous.close <= previous_zone.upper

            if breached and fully_inside:
                self._create_setup(
                    path=ScenarioPath.REJECTION,
                    context=context,
                    members=members,
                    bar=bar,
                    decision_index=index,
                    state=SetupState.WAITING_DISPLACEMENT,
                )
                continue
            if breached and outside_close and previous_inside:
                self._create_setup(
                    path=ScenarioPath.ACCEPTANCE,
                    context=context,
                    members=members,
                    bar=bar,
                    decision_index=index,
                    state=SetupState.WAITING_ACCEPTANCE_HOLD,
                )
                continue
            if breached:
                self._create_setup(
                    path=ScenarioPath.REJECTION,
                    context=context,
                    members=members,
                    bar=bar,
                    decision_index=index,
                    state=SetupState.WAITING_RECLAIM,
                )
                continue
            path = ScenarioPath.ROTATION if any(
                item.family is StructureFamily.CHANNEL for item in members
            ) else ScenarioPath.BOUNCE
            self._create_setup(
                path=path,
                context=context,
                members=members,
                bar=bar,
                decision_index=index,
                state=SetupState.WAITING_DISPLACEMENT,
            )
