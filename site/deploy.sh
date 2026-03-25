#!/bin/bash
# Deploy ContextPulse landing page to Cloudflare Pages
# Run from the site/ directory
# Requires: npx wrangler (or global install)

echo "Deploying contextpulse.ai to Cloudflare Pages..."
npx wrangler pages deploy . --project-name=contextpulse-site --branch=main

echo ""
echo "Done! Site should be live at https://contextpulse.ai"
echo "(Make sure DNS CNAME points contextpulse.ai to contextpulse-site.pages.dev)"
