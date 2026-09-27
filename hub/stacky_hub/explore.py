"""Slow, delay-tolerant exploration: stop -> look -> decide -> one bounded step -> repeat.

Why this shape (see docs/protocol.md "Exploration with delays"):
- A photo decision takes 1-10 s (upload + vision model), so the robot is never
  steered continuously from images. It is always STOPPED while it looks and thinks.
- Steps are closed-loop on the mBot (straight <cm>, turn <deg>), so link or LLM
  delay cannot change how far it goes, and every step reports what really happened.
- Goals from the LLM are anchored to the pose where the photo was taken. If the robot
  moved or turned while the LLM was thinking, the goal is still applied correctly.

The explorer only needs two small interfaces, so it runs the same against the real
robot (via MbotLink or the stackchan-mcp gateway) and against tests/sim_world.py.
"""
import asyncio
import dataclasses
import logging
import math
import time
from typing import Optional, Protocol

from . import limits as L

log = logging.getLogger("stacky.explore")


# ------------------------------------------------------------------ geometry
def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


@dataclasses.dataclass
class Pose:
    """Dead-reckoned pose. x/y in cm, th in degrees (CCW, 0 = heading at start)."""
    x: float = 0.0
    y: float = 0.0
    th: float = 0.0

    def copy(self):
        return dataclasses.replace(self)

    def apply(self, dist_cm, yaw_deg):
        """Integrate one motion outcome (measured by the robot)."""
        dist_cm = dist_cm or 0.0
        yaw_deg = yaw_deg or 0.0
        mid = math.radians(self.th + yaw_deg / 2.0)   # heading drift during a straight
        self.x += dist_cm * math.cos(mid)
        self.y += dist_cm * math.sin(mid)
        self.th = wrap180(self.th + yaw_deg)

    def point_ahead(self, dist_cm, rel_deg=0.0):
        a = math.radians(self.th + rel_deg)
        return self.x + dist_cm * math.cos(a), self.y + dist_cm * math.sin(a)

    def bearing_to(self, x, y):
        """Relative bearing (deg, + = left) and distance (cm) to a world point."""
        dx, dy = x - self.x, y - self.y
        return wrap180(math.degrees(math.atan2(dy, dx)) - self.th), math.hypot(dx, dy)


# ------------------------------------------------------------------ interfaces
@dataclasses.dataclass
class StepResult:
    reason: str                 # target | timeout | obstacle | cmd | button | watchdog | replaced | refused | lost
    dist_cm: Optional[float]
    yaw_deg: Optional[float]

    @property
    def interrupted(self):
        return self.reason not in ("target", "time")


class Robot(Protocol):
    async def front_cm(self) -> Optional[float]: ...
    async def straight(self, cm: float, speed_percent: float) -> StepResult: ...
    async def turn(self, deg: float, speed_percent: float) -> StepResult: ...
    async def stop(self) -> None: ...


class Eyes(Protocol):
    async def look(self, head_yaw_deg: float, question: str) -> Optional[str]:
        """Point the camera (head yaw relative to the body, + = left) and return an image path."""


# ------------------------------------------------------------------ state
@dataclasses.dataclass
class Observation:
    id: int
    pose: Pose                  # robot pose when the photo was taken (robot was stopped)
    head_yaw: float             # camera direction relative to the body
    image: Optional[str]
    t: float
    front_cm: Optional[float]

    def as_dict(self):
        return {"id": self.id, "image": self.image, "head_yaw": self.head_yaw,
                "pose": dataclasses.asdict(self.pose), "front_cm": self.front_cm,
                "age_s": round(time.time() - self.t, 1)}


@dataclasses.dataclass
class Goal:
    """A target set by the LLM, anchored in the world frame at decision time."""
    heading: float                      # world heading (deg)
    target: Optional[tuple] = None      # world point (x, y) if a distance was given
    note: str = ""
    set_at: float = 0.0
    tries: int = 0


@dataclasses.dataclass
class ExploreConfig:
    step_cm: float = 20.0
    speed_percent: float = 30.0
    turn_speed_percent: float = 30.0
    safe_cm: float = 25.0           # never start a step with less free space than this ahead
    scan_step_deg: float = 45.0     # when blocked, look around in these increments
    look_every: int = 3             # take photos every N steps (0 = never)
    head_angles: tuple = (-50.0, 0.0, 50.0)
    cell_cm: float = 20.0           # visit-map resolution
    max_steps: int = 60
    max_seconds: float = 600.0
    goal_reached_cm: float = 15.0
    max_goal_tries: int = 12


class Explorer:
    def __init__(self, robot: Robot, eyes: Optional[Eyes] = None, config: ExploreConfig = None):
        self.robot, self.eyes = robot, eyes
        self.cfg = config or ExploreConfig()
        self.pose = Pose()
        self.visits = {}                # (i, j) -> count
        self.blocked = set()            # cells where an obstacle was seen
        self.observations = []
        self.goal: Optional[Goal] = None
        self.last_goal_result = None    # {"note", "pose", "error_cm"} when a goal ends
        self.steps = 0
        self.state = "idle"             # idle | running | stopped | finished | stuck | error
        self.stop_reason = ""
        self.timing = {"sense_s": [], "step_s": []}
        self._stop = asyncio.Event()
        self.log = []                   # human-readable step log (last 100)

    # ---------------------------------------------------------- public control
    def request_stop(self, reason="requested"):
        self.stop_reason = reason
        self._stop.set()

    def set_goal_from_observation(self, obs_id, bearing_in_image_deg=0.0, distance_cm=None, note=""):
        """LLM says: "in photo #obs_id, go toward bearing X (+ = left of image centre), about D cm away".
        Anchored at the observation's pose, so the robot's movement since then is compensated."""
        obs = next((o for o in self.observations if o.id == obs_id), None)
        if obs is None:
            raise ValueError(f"no observation {obs_id}")
        rel = obs.head_yaw + bearing_in_image_deg
        heading = wrap180(obs.pose.th + rel)
        target = obs.pose.point_ahead(distance_cm, rel) if distance_cm else None
        self.goal = Goal(heading=heading, target=target, note=note, set_at=time.time())
        self._note(f"goal from obs {obs_id}: heading {heading:.0f}°" + (f", target {target[0]:.0f},{target[1]:.0f}" if target else ""))
        return self.goal

    def set_goal_relative(self, bearing_deg, distance_cm=None, note=""):
        """Goal relative to the robot's CURRENT pose (use when there is no photo to anchor to)."""
        heading = wrap180(self.pose.th + bearing_deg)
        target = self.pose.point_ahead(distance_cm, bearing_deg) if distance_cm else None
        self.goal = Goal(heading=heading, target=target, note=note, set_at=time.time())
        return self.goal

    def clear_goal(self):
        self.goal = None

    def status(self, last_obs=3):
        visited = len(self.visits)
        return {
            "state": self.state, "stop_reason": self.stop_reason, "steps": self.steps,
            "pose": {k: round(v, 1) for k, v in dataclasses.asdict(self.pose).items()},
            "goal": None if not self.goal else {
                "heading": round(self.goal.heading, 1), "note": self.goal.note,
                "target": None if not self.goal.target else [round(c, 1) for c in self.goal.target]},
            "last_goal": None if not self.last_goal_result else {
                "note": self.last_goal_result["note"], "error_cm": round(self.last_goal_result["error_cm"], 1)},
            "cells_visited": visited, "cells_blocked": len(self.blocked),
            "observations": [o.as_dict() for o in self.observations[-last_obs:]],
            "latency": {k: (round(sum(v) / len(v), 2) if v else None) for k, v in self.timing.items()},
            "log": self.log[-8:],
        }

    # ---------------------------------------------------------- main loop
    async def run(self):
        self.state, t_start = "running", time.monotonic()
        self._mark_visit()
        try:
            while not self._stop.is_set():
                if self.steps >= self.cfg.max_steps:
                    self.stop_reason = "max_steps"
                    break
                if time.monotonic() - t_start > self.cfg.max_seconds:
                    self.stop_reason = "max_seconds"
                    break
                ok = await self._iteration()
                if not ok:
                    break
            self.state = "stopped" if self._stop.is_set() else ("stuck" if self.stop_reason == "stuck" else "finished")
        except Exception as e:   # never leave the robot moving on a bug
            log.exception("explorer error")
            self.state, self.stop_reason = "error", f"{type(e).__name__}: {e}"
        finally:
            await self.robot.stop()
        return self.status()

    async def _iteration(self):
        # 1. LOOK (robot is stopped here, so every photo has an exact pose)
        t0 = time.monotonic()
        front = await self.robot.front_cm()
        if self.eyes and self.cfg.look_every and self.steps % self.cfg.look_every == 0:
            await self._look_around(front)
        self.timing["sense_s"].append(time.monotonic() - t0)

        # 2. DECIDE
        want = self._desired_heading(front)
        if want is None:
            want = await self._scan_for_open_heading()
            if want is None:
                self.stop_reason = "stuck"
                self._note("stuck: no open direction")
                return False
        turn = wrap180(want - self.pose.th)

        # 3. STEP (one bounded, closed-loop primitive), then re-sense
        t1 = time.monotonic()
        if abs(turn) > 8:
            r = await self.robot.turn(max(-L.MAX_TURN_DEG, min(L.MAX_TURN_DEG, turn)), self.cfg.turn_speed_percent)
            self._apply(r, 0.0, turn)
        else:
            front = await self.robot.front_cm()
            if front is not None and front < self.cfg.safe_cm:
                self._mark_blocked(front)
                return await self._handle_goal_blocked()
            cm = self.cfg.step_cm if front is None else min(self.cfg.step_cm, front - self.cfg.safe_cm + 5)
            cm = max(5.0, min(cm, L.MAX_STEP_CM))
            r = await self.robot.straight(cm, self.cfg.speed_percent)
            self._apply(r, cm, 0.0)
            if r.reason == "obstacle":
                self._mark_blocked(L.OBSTACLE_CM)
        self.timing["step_s"].append(time.monotonic() - t1)
        self.steps += 1
        if r.reason in ("button", "watchdog", "refused", "lost"):
            self.stop_reason = f"robot:{r.reason}"
            self._note(f"stopping: robot reported {r.reason}")
            return False
        self._check_goal()
        return True

    # ---------------------------------------------------------- decision rules
    def _desired_heading(self, front):
        if self.goal:
            if self.goal.target:
                bearing, _ = self.pose.bearing_to(*self.goal.target)
                return wrap180(self.pose.th + bearing)
            return self.goal.heading
        ahead = self._cell(*self.pose.point_ahead(self.cfg.cell_cm))
        if front is not None and front >= self.cfg.safe_cm and self.visits.get(ahead, 0) < 2 \
                and ahead not in self.blocked:
            return self.pose.th                      # keep going: open and new
        return None                                  # scan and choose

    async def _scan_for_open_heading(self):
        """Turn in place in scan_step increments, measure free space, pick the best new direction."""
        best, best_score = None, -1e9
        n = int(360 / self.cfg.scan_step_deg)
        for k in range(n):
            if self._stop.is_set():
                return None
            front = await self.robot.front_cm()
            h = self.pose.th
            free = 150.0 if front is None else min(front, 150.0)
            if free >= self.cfg.safe_cm:
                cell = self._cell(*self.pose.point_ahead(self.cfg.cell_cm))
                score = free - 40.0 * self.visits.get(cell, 0) - (100.0 if cell in self.blocked else 0.0)
                if score > best_score:
                    best, best_score = h, score
            else:
                self._mark_blocked(front)
            if k < n - 1:
                r = await self.robot.turn(self.cfg.scan_step_deg, self.cfg.turn_speed_percent)
                self._apply(r, 0.0, self.cfg.scan_step_deg)
                if r.interrupted and r.reason != "timeout":
                    return None
        self._note("scan: best heading " + ("none" if best is None else f"{best:.0f}°"))
        return best

    async def _handle_goal_blocked(self):
        if self.goal:
            self.goal.tries += 1
            if self.goal.tries > self.cfg.max_goal_tries:
                self._note("goal abandoned: blocked")
                self.goal = None
            else:   # detour: pick an open heading, the goal is re-aimed next iteration
                h = await self._scan_for_open_heading()
                if h is None:
                    self.stop_reason = "stuck"
                    return False
                r = await self.robot.turn(wrap180(h - self.pose.th), self.cfg.turn_speed_percent)
                self._apply(r, 0.0, 0.0)
                r = await self.robot.straight(self.cfg.step_cm, self.cfg.speed_percent)
                self._apply(r, self.cfg.step_cm, 0.0)
        self.steps += 1
        return True

    def _check_goal(self):
        g = self.goal
        if not g:
            return
        if g.target:
            _, dist = self.pose.bearing_to(*g.target)
            if dist <= self.cfg.goal_reached_cm:
                self._note(f"goal reached: {g.note}")
                self.last_goal_result = {"note": g.note, "pose": self.pose.copy(), "error_cm": dist}
                self.goal = None
        elif abs(wrap180(g.heading - self.pose.th)) <= 10:
            self._note(f"goal heading reached: {g.note}")
            self.goal = None

    # ---------------------------------------------------------- bookkeeping
    async def _look_around(self, front):
        for yaw in self.cfg.head_angles:
            img = await self.eyes.look(yaw, "exploring: what is here?")
            obs = Observation(id=len(self.observations) + 1, pose=self.pose.copy(), head_yaw=yaw,
                              image=img, t=time.time(), front_cm=front)
            self.observations.append(obs)
        await self.eyes.look(0.0, "")   # face forward again (no photo needed)

    def _apply(self, r: StepResult, planned_cm, planned_deg):
        # Trust the robot's measured outcome. Fall back to the plan only if it has no sensors.
        dist = r.dist_cm if r.dist_cm is not None else (planned_cm if not r.interrupted else 0.0)
        yaw = r.yaw_deg if r.yaw_deg is not None else (planned_deg if not r.interrupted else 0.0)
        self.pose.apply(dist, yaw)
        self._mark_visit()
        self._note(f"{r.reason}: moved {dist:.1f} cm, turned {yaw:.0f}° -> "
                   f"({self.pose.x:.0f},{self.pose.y:.0f}) {self.pose.th:.0f}°")

    def _cell(self, x, y):
        return int(math.floor(x / self.cfg.cell_cm)), int(math.floor(y / self.cfg.cell_cm))

    def _mark_visit(self):
        c = self._cell(self.pose.x, self.pose.y)
        self.visits[c] = self.visits.get(c, 0) + 1

    def _mark_blocked(self, front_cm):
        if front_cm is not None:
            self.blocked.add(self._cell(*self.pose.point_ahead(max(front_cm, 1.0) + self.cfg.cell_cm / 2)))

    def _note(self, text):
        log.info(text)
        self.log.append(text)
        del self.log[:-100]
