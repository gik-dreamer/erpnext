# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe

from frappe.utils import cint, cstr, flt, add_days, nowdate
from frappe import _, ValidationError

from erpnext.controllers.stock_controller import StockController

class SerialNoCannotCreateDirectError(ValidationError): pass
class SerialNoCannotCannotChangeError(ValidationError): pass
class SerialNoNotRequiredError(ValidationError): pass
class SerialNoRequiredError(ValidationError): pass
class SerialNoQtyError(ValidationError): pass
class SerialNoItemError(ValidationError): pass
class SerialNoWarehouseError(ValidationError): pass
class SerialNoStatusError(ValidationError): pass
class SerialNoNotExistsError(ValidationError): pass
class SerialNoDuplicateError(ValidationError): pass

class SerialNo(StockController):
	def __init__(self, arg1, arg2=None):
		super(SerialNo, self).__init__(arg1, arg2)
		self.via_stock_ledger = False

	def validate(self):
		if self.get("__islocal") and self.warehouse:
			frappe.throw(_("New Serial No cannot have Warehouse. Warehouse must be \
				set by Stock Entry or Purchase Receipt"), SerialNoCannotCreateDirectError)
			
		self.set_maintenance_status()
		self.validate_warehouse()
		self.validate_item()
		self.on_stock_ledger_entry()

	def set_maintenance_status(self):
		if not self.warranty_expiry_date and not self.amc_expiry_date:
			self.maintenance_status = None
			
		if self.warranty_expiry_date and self.warranty_expiry_date < nowdate():
			self.maintenance_status = "Out of Warranty"
		
		if self.amc_expiry_date and self.amc_expiry_date < nowdate():
			self.maintenance_status = "Out of AMC"
		
		if self.amc_expiry_date and self.amc_expiry_date >= nowdate():
			self.maintenance_status = "Under AMC"
			
		if self.warranty_expiry_date and self.warranty_expiry_date >= nowdate():
			self.maintenance_status = "Under Warranty"

	def validate_warehouse(self):
		if not self.get("__islocal"):
			item_code, warehouse = frappe.db.get_value("Serial No", 
				self.name, ["item_code", "warehouse"])
			if item_code != self.item_code:
				frappe.throw(_("Item Code cannot be changed for Serial No."), 
					SerialNoCannotCannotChangeError)
			if not self.via_stock_ledger and warehouse != self.warehouse:
				frappe.throw(_("Warehouse cannot be changed for Serial No."), 
					SerialNoCannotCannotChangeError)

	def validate_item(self):
		"""
			Validate whether serial no is required for this item
		"""
		item = frappe.get_doc("Item", self.item_code)
		if item.has_serial_no!="Yes":
			frappe.throw(_("Item must have 'Has Serial No' as 'Yes'") + ": " + self.item_code)
			
		self.item_group = item.item_group
		self.description = item.description
		self.item_name = item.item_name
		self.brand = item.brand
		self.warranty_period = item.warranty_period
		
	def set_status(self, last_sle):
		if last_sle:
			if last_sle.voucher_type == "Stock Entry":
				document_type = frappe.db.get_value("Stock Entry", last_sle.voucher_no, 
					"purpose")
			else:
				document_type = last_sle.voucher_type

			if last_sle.actual_qty > 0:
				if document_type == "Sales Return":
					self.status = "Sales Returned"
				else:
					self.status = "Available"
			else:
				if document_type == "Purchase Return":
					self.status = "Purchase Returned"
				elif last_sle.voucher_type in ("Delivery Note", "Sales Invoice"):
					self.status = "Delivered"
				else:
					self.status = "Not Available"
		else:
			self.status = "Not Available"
		
	def set_purchase_details(self, purchase_sle):
		if purchase_sle:
			self.purchase_document_type = purchase_sle.voucher_type
			self.purchase_document_no = purchase_sle.voucher_no
			self.purchase_date = purchase_sle.posting_date
			self.purchase_time = purchase_sle.posting_time
			self.purchase_rate = purchase_sle.incoming_rate
			if purchase_sle.voucher_type == "Purchase Receipt":
				self.supplier, self.supplier_name = \
					frappe.db.get_value("Purchase Receipt", purchase_sle.voucher_no, 
						["supplier", "supplier_name"])
		else:
			for fieldname in ("purchase_document_type", "purchase_document_no", 
				"purchase_date", "purchase_time", "purchase_rate", "supplier", "supplier_name"):
					self.set(fieldname, None)
				
	def set_sales_details(self, delivery_sle):
		if delivery_sle:
			self.delivery_document_type = delivery_sle.voucher_type
			self.delivery_document_no = delivery_sle.voucher_no
			self.delivery_date = delivery_sle.posting_date
			self.delivery_time = delivery_sle.posting_time
			self.customer, self.customer_name = \
				frappe.db.get_value(delivery_sle.voucher_type, delivery_sle.voucher_no, 
					["customer", "customer_name"])
			if self.warranty_period:
				self.warranty_expiry_date	= add_days(cstr(delivery_sle.posting_date), 
					cint(self.warranty_period))
		else:
			for fieldname in ("delivery_document_type", "delivery_document_no", 
				"delivery_date", "delivery_time", "customer", "customer_name", 
				"warranty_expiry_date"):
					self.set(fieldname, None)
							
	def get_last_sle(self):
		entries = {}
		sle_dict = self.get_stock_ledger_entries()
		if sle_dict:
			if sle_dict.get("incoming", []):
				entries["purchase_sle"] = sle_dict["incoming"][0]
		
			if len(sle_dict.get("incoming", [])) - len(sle_dict.get("outgoing", [])) > 0:
				entries["last_sle"] = sle_dict["incoming"][0]
			else:
				entries["last_sle"] = sle_dict["outgoing"][0]
				entries["delivery_sle"] = sle_dict["outgoing"][0]
				
		return entries
		
	def get_stock_ledger_entries(self):
		sle_dict = {}
		for sle in frappe.db.sql("""select * from `tabStock Ledger Entry` 
			where serial_no like %s and item_code=%s and ifnull(is_cancelled, 'No')='No' 
			order by posting_date desc, posting_time desc, name desc""", 
			("%%%s%%" % self.name, self.item_code), as_dict=1):
				if self.name.upper() in get_serial_nos(sle.serial_no):
					if sle.actual_qty > 0:
						sle_dict.setdefault("incoming", []).append(sle)
					else:
						sle_dict.setdefault("outgoing", []).append(sle)
					
		return sle_dict
					
	def on_trash(self):
		if self.status == 'Delivered':
			frappe.throw(_("Delivered Serial No ") + self.name + _(" can not be deleted"))
		if self.warehouse:
			frappe.throw(_("Cannot delete Serial No in warehouse. \
				First remove from warehouse, then delete.") + ": " + self.name)
	
	def before_rename(self, old, new, merge=False):
		if merge:
			frappe.throw(_("Sorry, Serial Nos cannot be merged"))
			
	def after_rename(self, old, new, merge=False):
		"""rename serial_no text fields"""
		for dt in frappe.db.sql("""select parent from tabDocField 
			where fieldname='serial_no' and fieldtype='Text'"""):
			
			for item in frappe.db.sql("""select name, serial_no from `tab%s` 
				where serial_no like '%%%s%%'""" % (dt[0], old)):
				
				serial_nos = map(lambda i: i==old and new or i, item[1].split('\n'))
				frappe.db.sql("""update `tab%s` set serial_no = %s 
					where name=%s""" % (dt[0], '%s', '%s'),
					('\n'.join(serial_nos), item[0]))
	
	def on_stock_ledger_entry(self):
		if self.via_stock_ledger and not self.get("__islocal"):
			last_sle = self.get_last_sle()
			self.set_status(last_sle.get("last_sle"))
			self.set_purchase_details(last_sle.get("purchase_sle"))
			self.set_sales_details(last_sle.get("delivery_sle"))
			self.set_maintenance_status()
			
	def on_communication(self):
		return

def process_serial_no(sle):
	item_det = get_item_details(sle.item_code)
	validate_serial_no(sle, item_det)
	update_serial_nos(sle, item_det)
					
def validate_serial_no(sle, item_det):
	if item_det.has_serial_no=="No":
		if sle.serial_no:
			frappe.throw(_("Serial Number should be blank for Non Serialized Item" + ": " 
				+ sle.item_code), SerialNoNotRequiredError)
	else:
		if sle.serial_no:
			serial_nos = get_serial_nos(sle.serial_no)
			if cint(sle.actual_qty) != flt(sle.actual_qty):
				frappe.throw(_("Serial No qty cannot be a fraction") + \
					(": %s (%s)" % (sle.item_code, sle.actual_qty)))
			if len(serial_nos) and len(serial_nos) != abs(cint(sle.actual_qty)):
				frappe.throw(_("Serial Nos do not match with qty") + \
					(": %s (%s)" % (sle.item_code, sle.actual_qty)), SerialNoQtyError)
					
			if len(serial_nos) != len(set(serial_nos)):
				frappe.throw(_("Duplicate Serial No entered against item") + 
					(": %s" % sle.item_code), SerialNoDuplicateError)
			
			for serial_no in serial_nos:
				if frappe.db.exists("Serial No", serial_no):
					sr = frappe.get_doc("Serial No", serial_no)
					
					if sr.item_code!=sle.item_code:
						frappe.throw(_("Serial No does not belong to Item") + 
							(": %s (%s)" % (sle.item_code, serial_no)), SerialNoItemError)
							
					if sr.warehouse and sle.actual_qty > 0:
						frappe.throw(_("Same Serial No") + ": " + sr.name + 
							_(" can not be received twice"), SerialNoDuplicateError)
					
					if sle.actual_qty < 0:
						if sr.warehouse!=sle.warehouse:
							frappe.throw(_("Serial No") + ": " + serial_no + 
								_(" does not belong to Warehouse") + ": " + sle.warehouse, 
								SerialNoWarehouseError)
					
						if sle.voucher_type in ("Delivery Note", "Sales Invoice") \
							and sr.status != "Available":
							frappe.throw(_("Serial No status must be 'Available' to Deliver") 
								+ ": " + serial_no, SerialNoStatusError)
				elif sle.actual_qty < 0:
					# transfer out
					frappe.throw(_("Serial No must exist to transfer out.") + \
						": " + serial_no, SerialNoNotExistsError)
		elif sle.actual_qty < 0 or not item_det.serial_no_series:
			frappe.throw(_("Serial Number Required for Serialized Item" + ": " 
				+ sle.item_code), SerialNoRequiredError)
				
def update_serial_nos(sle, item_det):
	if sle.is_cancelled == "No" and not sle.serial_no and sle.actual_qty > 0 and item_det.serial_no_series:
		from frappe.model.naming import make_autoname
		serial_nos = []
		for i in xrange(cint(sle.actual_qty)):
			serial_nos.append(make_autoname(item_det.serial_no_series))
		frappe.db.set(sle, "serial_no", "\n".join(serial_nos))
		
	if sle.serial_no:
		serial_nos = get_serial_nos(sle.serial_no)
		for serial_no in serial_nos:
			if frappe.db.exists("Serial No", serial_no):
				sr = frappe.get_doc("Serial No", serial_no)
				sr.via_stock_ledger = True
				sr.warehouse = sle.warehouse if sle.actual_qty > 0 else None
				sr.save()
			elif sle.actual_qty > 0:
				make_serial_no(serial_no, sle)

def get_item_details(item_code):
	return frappe.db.sql("""select name, has_batch_no, docstatus, 
		is_stock_item, has_serial_no, serial_no_series 
		from tabItem where name=%s""", item_code, as_dict=True)[0]
		
def get_serial_nos(serial_no):
	return [s.strip() for s in cstr(serial_no).strip().upper().replace(',', '\n').split('\n') 
		if s.strip()]

def make_serial_no(serial_no, sle):
	sr = frappe.new_doc("Serial No")
	sr.serial_no = serial_no
	sr.item_code = sle.item_code
	sr.warehouse = None
	sr.company = sle.company
	sr.via_stock_ledger = True
	sr.insert()
	sr.warehouse = sle.warehouse
	sr.status = "Available"
	sr.save()
	frappe.msgprint(_("Serial No created") + ": " + sr.name)
	return sr.name
	
def update_serial_nos_after_submit(controller, parentfield):
	stock_ledger_entries = frappe.db.sql("""select voucher_detail_no, serial_no
		from `tabStock Ledger Entry` where voucher_type=%s and voucher_no=%s""", 
		(controller.doctype, controller.name), as_dict=True)
		
	if not stock_ledger_entries: return

	for d in controller.get(parentfield):
		serial_no = None
		for sle in stock_ledger_entries:
			if sle.voucher_detail_no==d.name:
				serial_no = sle.serial_no
				break

		if d.serial_no != serial_no:
			d.serial_no = serial_no
			frappe.db.set_value(d.doctype, d.name, "serial_no", serial_no)