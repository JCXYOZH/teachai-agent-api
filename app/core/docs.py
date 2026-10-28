import html
import json

from fastapi.responses import HTMLResponse

SWAGGER_UI_ASSET_PATH = "/static/swagger-ui"


def swagger_ui_html(*, openapi_url: str, title: str) -> HTMLResponse:
    safe_title = html.escape(title)
    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <link rel="icon" href="{SWAGGER_UI_ASSET_PATH}/favicon-32x32.png" />
  <link rel="stylesheet" href="{SWAGGER_UI_ASSET_PATH}/swagger-ui.css" />
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{SWAGGER_UI_ASSET_PATH}/swagger-ui-bundle.js"></script>
  <script src="{SWAGGER_UI_ASSET_PATH}/swagger-ui-standalone-preset.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: {json.dumps(openapi_url)},
      dom_id: '#swagger-ui',
      deepLinking: true,
      displayRequestDuration: true,
      persistAuthorization: true,
      validatorUrl: null,
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIStandalonePreset
      ],
      plugins: [
        SwaggerUIBundle.plugins.DownloadUrl
      ],
      layout: 'StandaloneLayout'
    }});
  </script>
</body>
</html>
"""
    return HTMLResponse(content)
