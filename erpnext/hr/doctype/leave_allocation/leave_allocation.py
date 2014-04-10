# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import cint, flt
from frappe import msgprint
	
from frappe.model.document import Document

class LeaveAllocation(Document):
	def validate(self):
		self.validate_new_leaves_allocated_value()
		self.check_existing_leave_allocation()
		self.validate_new_leaves_allocated()
		
	def on_update_after_submit(self):
		self.validate_new_leaves_allocated_value()
		self.validate_new_leaves_allocated()

	def on_update(self):
		self.get_total_allocated_leaves()
		
	def on_cancel(self):
		self.check_for_leave_application()
		
	def validate_new_leaves_allocated_value(self):
		"""validate that leave allocation is in multiples of 0.5"""
		if flt(self.new_leaves_allocated) % 0.5:
			guess = round(flt(self.new_leaves_allocated) * 2.0) / 2.0
			
			msgprint("""New Leaves Allocated should be a multiple of 0.5.
				Perhaps you should enter %s or %s""" % (guess, guess + 0.5),
				raise_exception=1)
		
	def check_existing_leave_allocation(self):
		"""check whether leave for same type is already allocated or not"""
		leave_allocation = frappe.db.sql("""select name from `tabLeave Allocation`
			where employee=%s and leave_type=%s and fiscal_year=%s and docstatus=1""",
			(self.employee, self.leave_type, self.fiscal_year))
		if leave_allocation:
			msgprint("""%s is already allocated to Employee: %s for Fiscal Year: %s.
				Please refere Leave Allocation: \
				<a href="#Form/Leave Allocation/%s">%s</a>""" % \
				(self.leave_type, self.employee, self.fiscal_year,
				leave_allocation[0][0], leave_allocation[0][0]), raise_exception=1)
			
	def validate_new_leaves_allocated(self):
		"""check if Total Leaves Allocated >= Leave Applications"""
		self.total_leaves_allocated = flt(self.carry_forwarded_leaves) + \
			flt(self.new_leaves_allocated)
		leaves_applied = self.get_leaves_applied(self.fiscal_year)
		if leaves_applied > self.total_leaves_allocated:
			expected_new_leaves = flt(self.new_leaves_allocated) + \
				(leaves_applied - self.total_leaves_allocated)
			msgprint("""Employee: %s has already applied for %s leaves.
				Hence, New Leaves Allocated should be atleast %s""" % \
				(self.employee, leaves_applied, expected_new_leaves),
				raise_exception=1)
		
	def get_leave_bal(self, prev_fyear):
		return self.get_leaves_allocated(prev_fyear) - self.get_leaves_applied(prev_fyear)
		
	def get_leaves_applied(self, fiscal_year):
		leaves_applied = frappe.db.sql("""select SUM(ifnull(total_leave_days, 0))
			from `tabLeave Application` where employee=%s and leave_type=%s
			and fiscal_year=%s and docstatus=1""", 
			(self.employee, self.leave_type, fiscal_year))
		return leaves_applied and flt(leaves_applied[0][0]) or 0

	def get_leaves_allocated(self, fiscal_year):
		leaves_allocated = frappe.db.sql("""select SUM(ifnull(total_leaves_allocated, 0))
			from `tabLeave Allocation` where employee=%s and leave_type=%s
			and fiscal_year=%s and docstatus=1 and name!=%s""",
			(self.employee, self.leave_type, fiscal_year, self.name))
		return leaves_allocated and flt(leaves_allocated[0][0]) or 0
	
	def allow_carry_forward(self):
		"""check whether carry forward is allowed or not for this leave type"""
		cf = frappe.db.sql("""select is_carry_forward from `tabLeave Type` where name = %s""",
			self.leave_type)
		cf = cf and cint(cf[0][0]) or 0
		if not cf:
			frappe.db.set(self,'carry_forward',0)
			msgprint("Sorry! You cannot carry forward %s" % (self.leave_type),
				raise_exception=1)

	def get_carry_forwarded_leaves(self):
		if self.carry_forward:
			self.allow_carry_forward()
		prev_fiscal_year = frappe.db.sql("""select name from `tabFiscal Year` 
			where year_start_date = (select date_add(year_start_date, interval -1 year) 
				from `tabFiscal Year` where name=%s) 
			order by name desc limit 1""", self.fiscal_year)
		prev_fiscal_year = prev_fiscal_year and prev_fiscal_year[0][0] or ''
		prev_bal = 0
		if prev_fiscal_year and cint(self.carry_forward) == 1:
			prev_bal = self.get_leave_bal(prev_fiscal_year)
		ret = {
			'carry_forwarded_leaves': prev_bal,
			'total_leaves_allocated': flt(prev_bal) + flt(self.new_leaves_allocated)
		}
		return ret

	def get_total_allocated_leaves(self):
		leave_det = self.get_carry_forwarded_leaves()
		frappe.db.set(self,'carry_forwarded_leaves',flt(leave_det['carry_forwarded_leaves']))
		frappe.db.set(self,'total_leaves_allocated',flt(leave_det['total_leaves_allocated']))

	def check_for_leave_application(self):
		exists = frappe.db.sql("""select name from `tabLeave Application`
			where employee=%s and leave_type=%s and fiscal_year=%s and docstatus=1""",
			(self.employee, self.leave_type, self.fiscal_year))
		if exists:
			msgprint("""Cannot cancel this Leave Allocation as \
				Employee : %s has already applied for %s. 
				Please check Leave Application: \
				<a href="#Form/Leave Application/%s">%s</a>""" % \
				(self.employee, self.leave_type, exists[0][0], exists[0][0]))
			raise Exception
