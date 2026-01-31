# Integration Icon Guide

## Current Status

The integration icon has been added to this repository (`icon.png` and `logo.png` in the repository root), but it will **not appear automatically** in Home Assistant's Integrations page.

## Why the Icon Doesn't Show

Home Assistant loads integration icons from the centralized [Home Assistant Brands repository](https://github.com/home-assistant/brands), not from your local installation or the integration's repository. This is by design to:
- Ensure consistent branding across all integrations
- Reduce the size of integration packages
- Provide a centralized, curated location for all integration assets

## How to Make the Icon Visible

To display the custom icon in Home Assistant's UI, you need to submit it to the Home Assistant Brands repository:

### Step 1: Prepare Your Icon Files

The Brands repository requires:
- **icon.png**: 256x256 pixels, PNG format
- **logo.png**: 256x256 pixels, PNG format (optional but recommended)

The current icon is 747x763 pixels and needs to be resized to 256x256.

### Step 2: Resize the Icon

You can use any image editing tool to resize the icon:

```bash
# Using ImageMagick (if available)
convert icon.png -resize 256x256 icon-256.png

# Using Python Pillow
python3 -c "from PIL import Image; img = Image.open('icon.png'); img.thumbnail((256, 256)); img.save('icon-256.png')"
```

Or use online tools like:
- https://www.iloveimg.com/resize-image
- https://www.resizepixel.com/

### Step 3: Submit to Brands Repository

1. **Fork the repository**: Go to https://github.com/home-assistant/brands and click "Fork"

2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/brands.git
   cd brands
   ```

3. **Create the integration folder**:
   ```bash
   mkdir -p custom_integrations/smart_fan_controller
   ```

4. **Add your icon files**:
   - Copy your resized `icon.png` (256x256) to `custom_integrations/smart_fan_controller/icon.png`
   - Copy your resized `logo.png` (256x256) to `custom_integrations/smart_fan_controller/logo.png` (optional)

5. **Create a domain.json file** (if required):
   ```bash
   # Check if a domain.json is needed by reviewing other custom integrations
   ```

6. **Commit and push**:
   ```bash
   git add custom_integrations/smart_fan_controller/
   git commit -m "Add icons for smart_fan_controller integration"
   git push origin main
   ```

7. **Create a Pull Request**:
   - Go to your forked repository on GitHub
   - Click "Contribute" → "Open pull request"
   - Fill in the details explaining your integration
   - Wait for review and approval

### Step 4: Wait for Approval

Once your PR is merged:
- Home Assistant will automatically fetch the icon from the Brands CDN
- The icon will appear in the Integrations page for all users
- No changes to your integration code are needed

## Alternative: Local Development

For local development and testing, you cannot display custom icons directly in the Integrations UI. However:
- You can reference local icons in custom Lovelace cards
- Place images in `/config/www/` to access them via `/local/` URLs in dashboards

## References

- [Home Assistant Brands Repository](https://github.com/home-assistant/brands)
- [Brands Repository Guidelines](https://github.com/home-assistant/brands#readme)
- [Integration Manifest Documentation](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [Community Discussion on Custom Integration Icons](https://community.home-assistant.io/t/custom-component-integration-icon/191726)
