"""A tiny 2-D world to test the explorer: walls, boxes, a robot with the runtime's rules.

The simulated robot obeys the same contract as stacky_runtime.py v1.1:
bounded closed-loop straight/turn, obstacle guard below OBSTACLE_CM for forward motion,
and it reports what it really did. Optional link delay and odometry noise.
"""
import asyncio
import math
import random

import stacky_hub.limits as L
from stacky_hub.explore import StepResult, wrap180

SENSOR_OFFSET_CM = 8.0     # ultrasonic sits at the front of the chassis
SENSOR_HALF_CONE_DEG = 15.0  # ultrasonic beam is a cone, not a ray
ROBOT_RADIUS_CM = 9.0


class SimWorld:
    def __init__(self, w=240.0, h=180.0, boxes=((110, 70, 150, 110),)):
        self.w, self.h = w, h
        self.boxes = list(boxes)   # (x0, y0, x1, y1)

    def free(self, x, y, r=ROBOT_RADIUS_CM):
        if x < r or y < r or x > self.w - r or y > self.h - r:
            return False
        return all(not (x0 - r < x < x1 + r and y0 - r < y < y1 + r) for x0, y0, x1, y1 in self.boxes)

    def sonar(self, x, y, th_deg, max_cm=300.0):
        """Closest echo within the ultrasonic cone."""
        return min(self.ray(x, y, th_deg + a, max_cm)
                   for a in (-SENSOR_HALF_CONE_DEG, -7.5, 0.0, 7.5, SENSOR_HALF_CONE_DEG))

    def ray(self, x, y, th_deg, max_cm=300.0):
        dx, dy = math.cos(math.radians(th_deg)), math.sin(math.radians(th_deg))
        d = 0.0
        while d < max_cm:
            px, py = x + dx * d, y + dy * d
            if not self.free(px, py, r=0.0):
                return d
            d += 0.5
        return max_cm


class SimRobot:
    def __init__(self, world, x=40.0, y=40.0, th=0.0, delay_s=0.0, yaw_noise=0.0, seed=1):
        self.world, self.x, self.y, self.th = world, x, y, th
        self.delay_s, self.yaw_noise = delay_s, yaw_noise
        self.rng = random.Random(seed)
        self.min_clearance = 1e9
        self.commands = []
        self.stopped = 0
        self.inject = None          # e.g. "button": the next step reports this reason

    async def _lag(self):
        if self.delay_s:
            await asyncio.sleep(self.delay_s)

    async def front_cm(self):
        await self._lag()
        return max(0.0, self.world.sonar(self.x, self.y, self.th) - SENSOR_OFFSET_CM)

    async def straight(self, cm, speed_percent):
        await self._lag()
        self.commands.append(("straight", cm))
        assert abs(cm) <= L.MAX_STEP_CM + 1e-9, "explorer exceeded MAX_STEP_CM"
        if self.inject:
            r, self.inject = self.inject, None
            return StepResult(r, 0.0, 0.0)
        sign = 1 if cm >= 0 else -1
        moved = 0.0
        reason = "target"
        while moved < abs(cm) - 1e-9:
            if sign > 0 and self.world.sonar(self.x, self.y, self.th) - SENSOR_OFFSET_CM < L.OBSTACLE_CM:
                reason = "obstacle"
                break
            nx = self.x + sign * 0.5 * math.cos(math.radians(self.th))
            ny = self.y + sign * 0.5 * math.sin(math.radians(self.th))
            if not self.world.free(nx, ny):
                reason = "obstacle"   # bumped (would be a real collision)
                self.min_clearance = min(self.min_clearance, 0.0)
                break
            self.x, self.y = nx, ny
            moved += 0.5
            self.min_clearance = min(self.min_clearance, self._body_clearance())
        await self._lag()
        return StepResult(reason, sign * moved, 0.0)

    def _body_clearance(self):
        """Distance from the robot's body edge to the nearest obstacle in any direction."""
        return min(self.world.ray(self.x, self.y, a, 60.0) for a in range(0, 360, 15)) - ROBOT_RADIUS_CM

    async def turn(self, deg, speed_percent):
        await self._lag()
        self.commands.append(("turn", deg))
        assert abs(deg) <= L.MAX_TURN_DEG + 1e-9, "explorer exceeded MAX_TURN_DEG"
        if self.inject:
            r, self.inject = self.inject, None
            return StepResult(r, 0.0, 0.0)
        actual = deg + self.rng.gauss(0, self.yaw_noise) if self.yaw_noise else deg
        self.th = wrap180(self.th + actual)
        await self._lag()
        return StepResult("target", 0.0, actual)   # the gyro reports the ACTUAL angle

    async def stop(self):
        self.stopped += 1


class SimEyes:
    def __init__(self, robot):
        self.robot = robot
        self.photos = []

    async def look(self, head_yaw_deg, question):
        if not question:
            return None
        self.photos.append((self.robot.x, self.robot.y, self.robot.th, head_yaw_deg))
        return f"sim://photo/{len(self.photos)}"
