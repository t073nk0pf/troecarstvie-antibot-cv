from __future__ import annotations

import numpy as np

from src.antibot_cv.automation.actions import ActionRequest


class RecoveryItemsRuntimeMixin:
    def _use_item_recovery_after_cycle(
        self,
        frame: np.ndarray,
        *,
        reason: str,
        require_after_cycle: bool = True,
        open_hunt_after: bool | None = None,
        threshold_override: float | None = None,
    ) -> bool:
        config = self.config.item_recovery
        if self.config.dry_run or not config.enabled or (require_after_cycle and not config.use_after_cycle):
            return False
        health_threshold = (
            float(threshold_override)
            if threshold_override is not None
            else self._item_recovery_threshold("health")
        )
        prowess_threshold = (
            float(threshold_override)
            if threshold_override is not None
            else self._item_recovery_threshold("prowess")
        )
        should_open_hunt = config.open_hunt_after if open_hunt_after is None else bool(open_hunt_after)
        request = ActionRequest(
            "use_recovery_items",
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            dry_run=self.config.dry_run,
            metadata={
                "frame_hash": self._last_frame_hash,
                "reason": reason,
                "health_names": list(config.health_names),
                "prowess_names": list(config.prowess_names),
                "use_when_below_percent": threshold_override or config.use_when_below_percent,
                "health_use_when_below_percent": health_threshold,
                "prowess_use_when_below_percent": prowess_threshold,
                "force_use": config.force_use,
                "use_if_resources_missing": config.use_if_resources_missing,
                "open_hunt_after": should_open_hunt,
                "timeout_s": config.timeout_s,
                "health_restore_percent": config.health_restore_percent,
                "prowess_restore_percent": config.prowess_restore_percent,
                "max_uses_per_resource": config.max_uses_per_resource,
                "inventory_open_delay_ms": config.inventory_open_delay_ms,
                "confirm_delay_ms": config.confirm_delay_ms,
                "between_items_delay_ms": config.between_items_delay_ms,
            },
        )
        self.logger.log_event(
            "recovery_items_intended",
            state=self.state_machine.state.value,
            cycle_id=self.session.cycle_id,
            battle_id=self.session.battle_id,
            reason=reason,
            health_names=list(config.health_names),
            prowess_names=list(config.prowess_names),
            use_when_below_percent=threshold_override or config.use_when_below_percent,
            health_use_when_below_percent=health_threshold,
            prowess_use_when_below_percent=prowess_threshold,
            max_uses_per_resource=config.max_uses_per_resource,
            open_hunt_after=should_open_hunt,
            frame_hash=self._last_frame_hash,
        )
        return self.action_executor.execute(request)
