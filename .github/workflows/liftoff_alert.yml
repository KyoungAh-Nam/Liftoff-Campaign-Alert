name: Liftoff Campaign Alert

on:
  schedule:
    - cron: '0 1 * * *'   # UTC 01:00 = KST 10:00
    - cron: '0 6 * * *'   # UTC 06:00 = KST 15:00
  workflow_dispatch:        # Manual run button

jobs:
  alert:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests

      - name: Run campaign alert
        env:
          ACCOUNT_1_NAME:    ${{ secrets.ACCOUNT_1_NAME }}
          ACCOUNT_1_KEY:     ${{ secrets.ACCOUNT_1_KEY }}
          ACCOUNT_1_SECRET:  ${{ secrets.ACCOUNT_1_SECRET }}
          # Add more accounts below if needed
          # ACCOUNT_2_NAME:  ${{ secrets.ACCOUNT_2_NAME }}
          # ACCOUNT_2_KEY:   ${{ secrets.ACCOUNT_2_KEY }}
          # ACCOUNT_2_SECRET: ${{ secrets.ACCOUNT_2_SECRET }}
          SLACK_CHANNEL_ID:  ${{ secrets.SLACK_CHANNEL_ID }}
          SLACK_BOT_TOKEN:   ${{ secrets.SLACK_BOT_TOKEN }}
        run: python liftoff_alert.py
