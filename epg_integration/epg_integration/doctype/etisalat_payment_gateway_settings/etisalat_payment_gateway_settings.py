# Copyright (c) 2025, ParaLogic and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document
from payments.utils import create_payment_gateway
from frappe.integrations.utils import make_post_request, create_request_log, get_json
from base64 import b64encode, b64decode
import json


class EtisalatPaymentGatewaySettings(Document):
	supported_currencies = ("AED", "USD")
	production_url = "https://ipg.comtrust.ae:2443"
	sandbox_url = "https://demo-ipg.ctdev.comtrust.ae:2443"

	def on_update(self):
		create_payment_gateway("Etisalat Payment Gateway")
		frappe.utils.call_hook_method("payment_gateway_enabled", gateway="Etisalat Payment Gateway")

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(_("Please select another payment method. Etisalat Payment Gateway does not support transactions in currency '{0}'").format(
				currency
			))

	def get_payment_url(self, **kwargs):
		order_id = kwargs.get("order_id")
		if not order_id:
			frappe.throw(_("Order ID is not provided"))

		if len(order_id) > 16:
			order_id = order_id[-16:]

		return self.generate_einvoice(
			order_id=order_id,
			amount=flt(kwargs.get("amount")),
			currency=kwargs.get("currency"),
			payer_name=kwargs.get("payer_name"),
			payer_email=kwargs.get("payer_email"),
			payer_mobile=kwargs.get("payer_mobile"),
			order_name=kwargs.get("order_name"),
			order_info=kwargs.get("order_info"),
			reference_doctype=kwargs.get("reference_doctype"),
			reference_docname=kwargs.get("reference_docname"),
		)

	def generate_einvoice(
		self,
		order_id,
		amount,
		currency,
		payer_name,
		payer_email=None,
		payer_mobile=None,
		order_name=None,
		order_info=None,
		reference_doctype=None,
		reference_docname=None,
	):
		request_params = self.get_epg_request_params()

		body = {
			"Customer": self.customer_id,
			"OrderID": order_id,
			"OrderName": order_name or reference_docname or order_id,
			"OrderInfo": order_info,
			"Amount": flt(amount),
			"Currency": currency,
			"OverrideCapture": "Auto",
			"RegisterForRecurrence": "False",
			"InvoiceType": "Once",
			"CardHolderName": payer_name,
			"CardHolderEmail": payer_email,
			"CardHolderMobile": payer_mobile,
			"MerchantMessage": "No",
		}

		payload = {"GenerateEInvoice": body}
		integration_request = create_request_log(
			payload,
			service_name="Etisalat Payment Gateway",
			reference_doctype=reference_doctype,
			reference_docname=reference_docname,
		)

		try:
			response = make_post_request(request_params.url, headers=request_params.headers, json=payload)
			integration_request.db_set("output", get_json(response), commit=True)

			transaction = response.get("Transaction", {})
			if transaction.get("ResponseCode") != "0":
				error_message = _("Failed to generate payment link")
				error_description = transaction.get("ResponseDescription")
				if error_description:
					error_message += ": " + error_description

				frappe.throw(error_message)

			if not transaction.get("InvoiceURL"):
				frappe.throw(_("EPG did not provide InvoiceURL"))
			if not transaction.get("InvoiceNumber"):
				frappe.throw(_("EPG did not provide InvoiceNumber"))

			integration_request.db_set({
				"request_id": transaction.get("InvoiceNumber"),
				"url": transaction.get("InvoiceURL"),
			}, commit=True)

			return transaction.get("InvoiceURL")

		except Exception:
			integration_request.db_set({
				"status": "Failed",
				"error": frappe.get_traceback(),
			}, commit=True)
			raise

	def handle_transaction_webhook(self, data):
		webhook_request = create_request_log(
			data,
			service_name="Etisalat Payment Gateway",
			is_remote_request=1,
		)

		try:
			encrypted_transaction = data.get("eInvoiceTransactionDetails")
			if not encrypted_transaction:
				frappe.throw(_("eInvoiceTransactionDetails is not provided"))

			decryption_key = self.get_password("decryption_key").encode("utf-8")
			key = decryption_key[:32]
			iv = decryption_key[32:]

			transaction_json = decrypt_aes_cbc(encrypted_transaction, key, iv=iv)

			data["eInvoiceTransactionDetails_decrypted"] = transaction_json
			webhook_request.db_set("data", get_json(data), commit=True)

			transaction = json.loads(transaction_json)

			data["eInvoiceTransactionDetails_decrypted"] = transaction
			webhook_request.db_set("data", get_json(data), commit=True)

			if not transaction.get("InvoiceID"):
				frappe.throw(_("InvoiceID is not provided"))

			webhook_request.db_set("request_id", transaction.get("InvoiceID"), commit=True)

			original_request = frappe.db.get_value("Integration Request", {
				"request_id": transaction.get("InvoiceID"),
				"integration_request_service": "Etisalat Payment Gateway",
				"is_remote_request": 0,
			})
			if not original_request:
				frappe.throw(_("Invalid InvoiceID"))

			original_request = frappe.get_doc("Integration Request", original_request)

			if original_request.reference_doctype and original_request.reference_docname:
				webhook_request.db_set({
					"reference_doctype": original_request.get("reference_doctype"),
					"reference_docname": original_request.get("reference_docname"),
				}, commit=True)

			if transaction.get("TransactionResponseCode") == "0":
				webhook_request.db_set("status", "Authorized", commit=True)
				original_request.db_set("status", "Authorized", commit=True)

				frappe.flags.data = data
				if original_request.reference_doctype and original_request.reference_docname:
					reference_doc = frappe.get_doc(original_request.reference_doctype, original_request.reference_docname)
					reference_doc.run_method(
						"on_payment_authorized",
						"Completed",
						reference_no=transaction.get("InvoiceID"),
					)
					frappe.db.commit()

				webhook_request.db_set("status", "Completed", commit=True)
				original_request.db_set("status", "Completed", commit=True)
			else:
				webhook_request.db_set({
					"status": "Failed",
					"error": transaction.get("TransactionResponseDescription"),
				}, commit=True)

		except Exception:
			webhook_request.db_set({
				"status": "Failed",
				"error": frappe.get_traceback(),
			}, commit=True)
			raise

	def get_epg_request_params(self):
		token_str = f"{self.api_username}:{self.get_password('api_password')}"
		token = b64encode(token_str.encode()).decode("utf-8")

		headers = frappe._dict({
			"Content-Type": "application/json",
			"Accept": "application/json",
			"Authorization": f"Basic {token}"
		})

		return frappe._dict({
			"url": self.sandbox_url if self.use_sandbox else self.production_url,
			"headers": headers,
		})


@frappe.whitelist(allow_guest=True, xss_safe=True)
def transaction_status_webhook(**kwargs):
	frappe.set_user("Administrator")

	try:
		data = frappe._dict(kwargs)
		settings = frappe.get_doc("Etisalat Payment Gateway Settings")
		settings.handle_transaction_webhook(data)
	except Exception:
		frappe.log_error(message=frappe.get_traceback())
		raise

	return {
		"ResponseCode": "0",
		"ResponseDescription": "Request Processed Successfully",
	}


def decrypt_aes_cbc(encrypted_data_b64, key, iv):
	from Crypto.Cipher import AES
	from Crypto.Util.Padding import unpad

	encrypted_data = b64decode(encrypted_data_b64)

	cipher = AES.new(key, AES.MODE_CBC, iv=iv)
	decrypted_padded_data = cipher.decrypt(encrypted_data)
	plaintext_bytes = unpad(decrypted_padded_data, AES.block_size)
	return plaintext_bytes.decode('utf-16')
