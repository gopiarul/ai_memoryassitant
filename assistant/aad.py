# assistant/ai.py

from google import genai
from google.genai import types
from django.conf import settings

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def analyze_image(image_file, question):
    try:
        image_bytes = image_file.read()

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=question),   # ✅ FIXED
                        types.Part.from_bytes(
                            data=image_bytes,
                            mime_type=image_file.content_type,
                        ),
                    ],
                )
            ],
        )

        # ✅ SAFE RESPONSE EXTRACTION (Improved)
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content.parts:
                return candidate.content.parts[0].text

        return "AI could not analyze this image."

    except Exception as e:
        print("IMAGE ERROR:", e)
        return f"AI Error: {str(e)}"