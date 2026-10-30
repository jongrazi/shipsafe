# ShipSafe

An AI risk analyst for small online shops that checks each new PayPal order for fraud warning signs and explains, in plain language, whether it is safe to ship.

Built for the PayPal AI Hackathon. This prototype runs only on the PayPal sandbox, so no real money moves.

## Status

Early setup. The demo store, PayPal order flow and verified webhook are in place, and each paid order gets a field report. The AI review is not built yet.

## Run it locally

You need Python 3.10 or later and a [PayPal sandbox app](https://developer.paypal.com/dashboard/applications/sandbox).

1. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv
   ```

   ```bash
   .venv/Scripts/python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your sandbox client ID and secret.
3. Start the app:

   ```bash
   .venv/Scripts/python -m uvicorn app.main:app --reload
   ```

4. Open <http://localhost:8000> and buy the item with a sandbox personal account.

The server log prints a field report for the order. The full PayPal response is saved in `data/samples`, which git ignores.

## Receive webhooks

PayPal needs a public HTTPS address, so webhooks need a deployment. `render.yaml` describes one on Render.

1. Deploy the repo and set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` on the service.
2. In the sandbox app, add a webhook for `https://<your-service>.onrender.com/webhooks/paypal`, subscribed to the `PAYMENT.CAPTURE.COMPLETED` event.
3. Set `PAYPAL_WEBHOOK_ID` on the service to the ID PayPal shows.

Webhooks are refused until that ID is set.

## Tests

```bash
.venv/Scripts/python -m pytest
```

## License

[MIT](LICENSE)
