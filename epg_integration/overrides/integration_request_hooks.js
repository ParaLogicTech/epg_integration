frappe.ui.form.on("Integration Request", {
	refresh: function (frm) {
		if (
			frm.doc.integration_request_service == "Etisalat Payment Gateway"
			&& frm.doc.is_remote_request
			&& frm.doc.status == "Failed"
			&& frm.doc.data
		) {
			frm.add_custom_button(__("Retry Processing"), () => {
				frappe.confirm(
					__("Are you sure you want to process this failed transaction status webhook request again?"),
					() => frm.events.retry_transaction_webhook(frm)
				);
			});
		}
	},

	retry_transaction_webhook(frm) {
		return frm.call({
			method: "epg_integration.epg_integration.doctype.etisalat_payment_gateway_settings.etisalat_payment_gateway_settings.retry_transaction_webhook",
			args: {
				integration_request: frm.doc.name,
			},
			freeze: 1,
			freeze_message: __("Processing..."),
			callback: () => {
				frm.reload_doc();
			}
		});
	},
});
