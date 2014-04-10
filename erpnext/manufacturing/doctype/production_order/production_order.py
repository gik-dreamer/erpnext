# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe

from frappe.utils import cstr, flt, nowdate
from frappe import msgprint, _

class OverProductionError(frappe.ValidationError): pass

from frappe.model.document import Document

class ProductionOrder(Document):

	def validate(self):
		if self.docstatus == 0:
			self.status = "Draft"
			
		from erpnext.utilities import validate_status
		validate_status(self.status, ["Draft", "Submitted", "Stopped", 
			"In Process", "Completed", "Cancelled"])

		self.validate_bom_no()
		self.validate_sales_order()
		self.validate_warehouse()
		
		from erpnext.utilities.transaction_base import validate_uom_is_integer
		validate_uom_is_integer(self, "stock_uom", ["qty", "produced_qty"])
		
	def validate_bom_no(self):
		if self.bom_no:
			bom = frappe.db.sql("""select name from `tabBOM` where name=%s and docstatus=1 
				and is_active=1 and item=%s"""
				, (self.bom_no, self.production_item), as_dict =1)
			if not bom:
				frappe.throw("""Incorrect BOM: %s entered. 
					May be BOM not exists or inactive or not submitted 
					or for some other item.""" % cstr(self.bom_no))
					
	def validate_sales_order(self):
		if self.sales_order:
			so = frappe.db.sql("""select name, delivery_date from `tabSales Order` 
				where name=%s and docstatus = 1""", self.sales_order, as_dict=1)[0]

			if not so.name:
				frappe.throw("Sales Order: %s is not valid" % self.sales_order)

			if not self.expected_delivery_date:
				self.expected_delivery_date = so.delivery_date
			
			self.validate_production_order_against_so()
			
	def validate_warehouse(self):
		from erpnext.stock.utils import validate_warehouse_company
		
		for w in [self.fg_warehouse, self.wip_warehouse]:
			validate_warehouse_company(w, self.company)
	
	def validate_production_order_against_so(self):
		# already ordered qty
		ordered_qty_against_so = frappe.db.sql("""select sum(qty) from `tabProduction Order`
			where production_item = %s and sales_order = %s and docstatus < 2 and name != %s""", 
			(self.production_item, self.sales_order, self.name))[0][0]

		total_qty = flt(ordered_qty_against_so) + flt(self.qty)
		
		# get qty from Sales Order Item table
		so_item_qty = frappe.db.sql("""select sum(qty) from `tabSales Order Item` 
			where parent = %s and item_code = %s""", 
			(self.sales_order, self.production_item))[0][0]
		# get qty from Packing Item table
		dnpi_qty = frappe.db.sql("""select sum(qty) from `tabPacked Item` 
			where parent = %s and parenttype = 'Sales Order' and item_code = %s""", 
			(self.sales_order, self.production_item))[0][0]
		# total qty in SO
		so_qty = flt(so_item_qty) + flt(dnpi_qty)
				
		if total_qty > so_qty:
			frappe.throw(_("Total production order qty for item") + ": " + 
				cstr(self.production_item) + _(" against sales order") + ": " + 
				cstr(self.sales_order) + _(" will be ") + cstr(total_qty) + ", " + 
				_("which is greater than sales order qty ") + "(" + cstr(so_qty) + ")" + 
				_("Please reduce qty."), exc=OverProductionError)

	def stop_unstop(self, status):
		""" Called from client side on Stop/Unstop event"""
		self.update_status(status)
		qty = (flt(self.qty)-flt(self.produced_qty)) * ((status == 'Stopped') and -1 or 1)
		self.update_planned_qty(qty)
		msgprint("Production Order has been %s" % status)


	def update_status(self, status):
		if status == 'Stopped':
			frappe.db.set(self, 'status', cstr(status))
		else:
			if flt(self.qty) == flt(self.produced_qty):
				frappe.db.set(self, 'status', 'Completed')
			if flt(self.qty) > flt(self.produced_qty):
				frappe.db.set(self, 'status', 'In Process')
			if flt(self.produced_qty) == 0:
				frappe.db.set(self, 'status', 'Submitted')


	def on_submit(self):
		if not self.wip_warehouse:
			frappe.throw(_("WIP Warehouse required before Submit"))
		frappe.db.set(self,'status', 'Submitted')
		self.update_planned_qty(self.qty)
		

	def on_cancel(self):
		# Check whether any stock entry exists against this Production Order
		stock_entry = frappe.db.sql("""select name from `tabStock Entry` 
			where production_order = %s and docstatus = 1""", self.name)
		if stock_entry:
			frappe.throw("""Submitted Stock Entry %s exists against this production order. 
				Hence can not be cancelled.""" % stock_entry[0][0])

		frappe.db.set(self,'status', 'Cancelled')
		self.update_planned_qty(-self.qty)

	def update_planned_qty(self, qty):
		"""update planned qty in bin"""
		args = {
			"item_code": self.production_item,
			"warehouse": self.fg_warehouse,
			"posting_date": nowdate(),
			"planned_qty": flt(qty)
		}
		from erpnext.stock.utils import update_bin
		update_bin(args)

@frappe.whitelist()	
def get_item_details(item):
	res = frappe.db.sql("""select stock_uom, description
		from `tabItem` where (ifnull(end_of_life, "")="" or end_of_life > now())
		and name=%s""", item, as_dict=1)
	
	if not res:
		return {}
		
	res = res[0]
	bom = frappe.db.sql("""select name from `tabBOM` where item=%s 
		and ifnull(is_default, 0)=1""", item)
	if bom:
		res.bom_no = bom[0][0]
		
	return res

@frappe.whitelist()
def make_stock_entry(production_order_id, purpose):
	production_order = frappe.get_doc("Production Order", production_order_id)
		
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.purpose = purpose
	stock_entry.production_order = production_order_id
	stock_entry.company = production_order.company
	stock_entry.bom_no = production_order.bom_no
	stock_entry.use_multi_level_bom = production_order.use_multi_level_bom
	stock_entry.fg_completed_qty = flt(production_order.qty) - flt(production_order.produced_qty)
	
	if purpose=="Material Transfer":
		stock_entry.to_warehouse = production_order.wip_warehouse
	else:
		stock_entry.from_warehouse = production_order.wip_warehouse
		stock_entry.to_warehouse = production_order.fg_warehouse
		
	stock_entry.run_method("get_items")
	return stock_entry.as_dict()