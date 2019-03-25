import argparse
import time
import msgpack
import utm
from enum import Enum, auto

import numpy as np
from planning_utils import a_star, find_start_goal, collinearity, heading
from grid import create_grid

from skimage.morphology import medial_axis
from skimage.util import invert
import numpy.linalg as LA
from random import randrange, uniform

from udacidrone import Drone
from udacidrone.connection import MavlinkConnection
from udacidrone.messaging import MsgID
from udacidrone.frame_utils import global_to_local, local_to_global


class States(Enum):
    MANUAL = auto()
    ARMING = auto()
    TAKEOFF = auto()
    WAYPOINT = auto()
    LANDING = auto()
    DISARMING = auto()
    PLANNING = auto()


class MotionPlanning(Drone):

    def __init__(self, connection):
        super().__init__(connection)

        self.target_position = np.array([0.0, 0.0, 0.0])
        self.waypoints = []
        self.in_mission = True
        self.check_state = {}

        # initial state
        self.flight_state = States.MANUAL

        # register all your callbacks here
        self.register_callback(MsgID.LOCAL_POSITION, self.local_position_callback)
        self.register_callback(MsgID.LOCAL_VELOCITY, self.velocity_callback)
        self.register_callback(MsgID.STATE, self.state_callback)

    def local_position_callback(self):
        if self.flight_state == States.TAKEOFF:
            if -1.0 * self.local_position[2] > 0.95 * self.target_position[2]:
                self.waypoint_transition()
        elif self.flight_state == States.WAYPOINT:
            if np.linalg.norm(self.target_position[0:2] - self.local_position[0:2]) < 5.0:
                if len(self.waypoints) > 0:
                    self.waypoint_transition()
                else:
                    if np.linalg.norm(self.local_velocity[0:2]) < 1.0:
                        self.landing_transition()

    def velocity_callback(self):
        if self.flight_state == States.LANDING:
            if self.global_position[2] - self.global_home[2] < 0.1:
                if abs(self.local_position[2]) < 0.01:
                    self.disarming_transition()

    def state_callback(self):
        if self.in_mission:
            if self.flight_state == States.MANUAL:
                self.arming_transition()
            elif self.flight_state == States.ARMING:
                if self.armed:
                    self.plan_path()
            elif self.flight_state == States.PLANNING:
                self.takeoff_transition()
            elif self.flight_state == States.DISARMING:
                if ~self.armed & ~self.guided:
                    self.manual_transition()

    def arming_transition(self):
        self.flight_state = States.ARMING
        print("arming transition")
        self.arm()
        self.take_control()

    def takeoff_transition(self):
        self.flight_state = States.TAKEOFF
        print("takeoff transition")
        self.takeoff(self.target_position[2])

    def waypoint_transition(self):
        self.flight_state = States.WAYPOINT
        print("waypoint transition")
        self.target_position = self.waypoints.pop(0)
        print('target position', self.target_position)
        self.cmd_position(self.target_position[0], self.target_position[1], self.target_position[2], self.target_position[3])

    def landing_transition(self):
        self.flight_state = States.LANDING
        print("landing transition")
        self.land()

    def disarming_transition(self):
        self.flight_state = States.DISARMING
        print("disarm transition")
        self.disarm()
        self.release_control()

    def manual_transition(self):
        self.flight_state = States.MANUAL
        print("manual transition")
        self.stop()
        self.in_mission = False

    def send_waypoints(self):
        print("Sending waypoints to simulator ...")
        data = msgpack.dumps(self.waypoints)
        self.connection._master.write(data)

    def plan_path(self):
        self.flight_state = States.PLANNING
        print("Searching for a path ...")
        TARGET_ALTITUDE = 5
        SAFETY_DISTANCE = 5

        self.target_position[2] = TARGET_ALTITUDE

        
        # DONE: read lat0, lon0 from colliders into floating point values    
        # for max_rows attribute a numpy vers. >= 1.16 is required, make sure to have an up to date scikit-image package 
        
        # data_pos = np.loadtxt('colliders.csv',dtype='str', max_rows=1)
        # (lat0,lon0) = [float(data_pos[1][:-1]),float(data_pos[3][:-1])]
        
        # for numpy vers <1.16 
        with open('colliders.csv') as f:
            latLonStrArr = f.readline().rstrip().replace('lat0','').replace('lon0 ','').split(',')
            lat0 = float(latLonStrArr[0])
            lon0 = float(latLonStrArr[1])

        # DONE: set home position to (lon0, lat0, 0)
        self.set_home_position(lon0, lat0, 0)
        # DONE: retrieve current global position
        global_position = [self._longitude, self._latitude, self._altitude]
        # DONE: convert to current local position using global_to_local()        
        current_local_pos = global_to_local(global_position,self.global_home)
        print('global home {0}, position {1}, local position {2}'.format(self.global_home, self.global_position,
                                                                         self.local_position))
        

        # Read in obstacle map
        data = np.loadtxt('colliders.csv', delimiter=',', dtype='Float64', skiprows=2)
        
        grid, north_offset, east_offset = create_grid(data, TARGET_ALTITUDE, SAFETY_DISTANCE)
        print("North offset = {0}, east offset = {1}".format(north_offset, east_offset))
        
        skeleton = medial_axis(invert(grid))

        # DONE: convert start position to current position rather than map center
        # Define starting point on the grid (this is just grid center)
        start_ne = (int(self.local_position[0]-north_offset), int(self.local_position[1]-east_offset))

        # Set goal as some arbitrary position on the grid
        # arb_goal = (750, 370, 0)
        
        # Set random goal on map in local coordinate system
        found = False
        while not found:
            goal_ne=(randrange(0,len(grid[:,1]-1)),randrange(0,len(grid[1,:]-1)))
            if grid[goal_ne] == 0:
                found = True

        # DONE: adapt to set goal as latitude / longitude position and convert (can be)
        # global_goal = local_to_global(arb_goal, self.global_home)
        # local_goal to show expected transformation
        # goal_ne = global_to_local(global_goal, self.global_home)
        print("Drone is starting from {0} and the goal was randomly set to {1}".format(start_ne,goal_ne))
        skel_start, skel_goal = find_start_goal(skeleton, start_ne, goal_ne)
        
        # Run A* to find a path from start to goal
        # DONE: add diagonal motions with a cost of sqrt(2) to your A* implementation
        # or move to a different search space such as a graph (not done here)
        path_, cost = a_star(invert(skeleton).astype(np.int), tuple(skel_start), tuple(skel_goal))
        print("Path length = {0}, path cost = {1}".format(len(path_), cost))
        # DONE: prune path to minimize number of waypoints
        path = collinearity(path_)

        # TODO (if you're feeling ambitious): Try a different approach altogether!

        # Convert path to waypoints
        waypoints = [[int(p[0]) + north_offset, int(p[1]) + east_offset, TARGET_ALTITUDE] for p in path]

        # get heading angle for next point
        theta = heading(path)
        for i in range(len(waypoints)):
            waypoints[i].append(theta[i])

        print(waypoints)
        # Set self.waypoints
        self.waypoints = waypoints
        # send waypoints to sim
        self.send_waypoints()

    def start(self):
        self.start_log("Logs", "NavLog.txt")
        print("starting connection")
        self.connection.start()
        self.stop_log()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5760, help='Port number')
    parser.add_argument('--host', type=str, default='127.0.0.1', help="host address, i.e. '127.0.0.1'")
    args = parser.parse_args()

    conn = MavlinkConnection('tcp:{0}:{1}'.format(args.host, args.port), timeout=60)
    drone = MotionPlanning(conn)
    time.sleep(1)

    drone.start()
