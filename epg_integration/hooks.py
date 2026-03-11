app_name = "epg_integration"
app_title = "EPG Integration"
app_publisher = "ParaLogic"
app_description = "Etisalat Payment Gateway Integration"
app_email = "info@paralogic.io"
app_license = "gpl-3.0"

override_whitelisted_methods = {
	"epg_integration.transaction_status_webhook": "epg_integration.epg_integration.doctype.etisalat_payment_gateway_settings.etisalat_payment_gateway_settings.transaction_status_webhook"
}

doctype_js = {
	"Integration Request": "overrides/integration_request_hooks.js"
}
