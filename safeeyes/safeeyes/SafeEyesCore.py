# Safe Eyes is a utility to remind you to take break frequently
# to protect your eyes from eye strain.

# Copyright (C) 2016  Gobinath

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import  Gdk, Gio, GLib, GdkX11
from apscheduler.scheduler import Scheduler
import time, threading, sys, subprocess, logging

logging.basicConfig()

class SafeEyesCore:

	def __init__(self, show_notification, start_break, end_break, on_countdown):
		# Initialize the variables
		self.break_count = 0
		self.long_break_message_index = 0
		self.short_break_message_index = 0
		self.skipped = False
		self.scheduler = None
		self.show_notification = show_notification
		self.start_break = start_break
		self.end_break = end_break
		self.on_countdown = on_countdown
		self.notification_condition = threading.Condition()

	"""
		Initialize the internal properties from configuration
	"""
	def initialize(self, config):
		self.short_break_messages = config['short_break_messages']
		self.long_break_messages = config['long_break_messages']
		self.no_of_short_breaks_per_long_break = config['no_of_short_breaks_per_long_break']
		self.pre_break_warning_time = config['pre_break_warning_time']
		self.long_break_duration = config['long_break_duration']
		self.short_break_duration = config['short_break_duration']
		self.break_interval = config['break_interval']

	"""
		Scheduler task to execute during every interval
	"""
	def scheduler_job(self):
		if not self.active:
			return

		# Pause the scheduler until the break
		if self.scheduler and self.scheduled_job_id:
			self.scheduler.unschedule_job(self.scheduled_job_id)
			self.scheduled_job_id = None

		GLib.idle_add(lambda: self.process_job())

	"""
		Used to process the job in default thread because is_full_screen_app_found must be run by default thread
	"""
	def process_job(self):
		if self.is_full_screen_app_found():
			# If full screen app found, do not show break screen.
			# Resume the scheduler
			if self.scheduler:
				self.schedule_job()
			return

		self.break_count = ((self.break_count + 1) % self.no_of_short_breaks_per_long_break)

		thread = threading.Thread(target=self.notify_and_start_break)
		thread.start()

	"""
		Show notification and start the break after given number of seconds
	"""
	def notify_and_start_break(self):
		# Show a notification
		self.show_notification()

		# Wait for the pre break warning period
		self.notification_condition.acquire()
		self.notification_condition.wait(self.pre_break_warning_time)
		self.notification_condition.release()

		# User can disable SafeEyes during notification
		if self.active:
			message = ""
			if self.is_long_break():
				self.long_break_message_index = (self.long_break_message_index + 1) % len(self.long_break_messages)
				message = self.long_break_messages[self.long_break_message_index]
			else:
				self.short_break_message_index = (self.short_break_message_index + 1) % len(self.short_break_messages)
				message = self.short_break_messages[self.short_break_message_index]
			
			# Show the break screen
			self.start_break(message)

			# Start the countdown
			seconds = 0
			if self.is_long_break():
				seconds = self.long_break_duration
			else:
				seconds = self.short_break_duration

			while seconds and self.active and not self.skipped:
				mins, secs = divmod(seconds, 60)
				timeformat = '{:02d}:{:02d}'.format(mins, secs)
				self.on_countdown(timeformat)
				time.sleep(1)	# Sleep for 1 second
				seconds -= 1

			# Loop terminated because of timeout (not skipped) -> Close the break alert
			if not self.skipped:
				self.end_break()

			# Resume the scheduler
			if self.active:
				if self.scheduler:
					self.schedule_job()

			self.skipped = False

	"""
		Check if the current break is long break or short current
	"""
	def is_long_break(self):
		return self.break_count == self.no_of_short_breaks_per_long_break - 1

	# User skipped the break using Skip button
	def skip_break(self):
		self.skipped = True

	"""
		Reschedule the job
	"""
	def toggle_active_state(self):
		if self.active:
			self.active = False
			if self.scheduler and self.scheduled_job_id:
				self.scheduler.unschedule_job(self.scheduled_job_id)
				self.scheduled_job_id = None

			# If waiting after notification, notify the thread to wake up and die
			self.notification_condition.acquire()
			self.notification_condition.notify()
			self.notification_condition.release()
		else:
			self.active = True
			if self.scheduler:
				self.schedule_job()

	"""
		Unschedule the job and shutdown the scheduler
	"""
	def stop(self):
		if self.scheduler:
			self.active = False
			if self.scheduled_job_id:
				self.scheduler.unschedule_job(self.scheduled_job_id)
				self.scheduled_job_id = None
			self.scheduler.shutdown(wait=False)
			self.scheduler = None

		# If waiting after notification, notify the thread to wake up and die
		self.notification_condition.acquire()
		self.notification_condition.notify()
		self.notification_condition.release()
	
	"""
		Schedule the job and start the scheduler
	"""
	def start(self):
		self.active = True
		if not self.scheduler:
			self.scheduler = Scheduler()
			self.schedule_job()
		self.scheduler.start()

	"""
		Restart the scheduler after changing settings
	"""
	def restart(self):
		if self.active:
			self.stop()
			self.start()

	"""
		Schedule the job
	"""
	def schedule_job(self):
		self.scheduled_job_id = self.scheduler.add_interval_job(self.scheduler_job, minutes=self.break_interval)

	"""
		Check for full-screen applications
	"""
	def is_full_screen_app_found(self):
		screen = Gdk.Screen.get_default()
		active_xid = str(screen.get_active_window().get_xid())
		cmdlist = ['xprop', '-root', '-notype','-id',active_xid, '_NET_WM_STATE']
		
		try:
			stdout = subprocess.check_output(cmdlist)
		except subprocess.CalledProcessError:
			pass
		else:
			if stdout:
				return 'FULLSCREEN' in stdout

