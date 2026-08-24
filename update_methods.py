import re
with open("routes/studio/studio_routes.py", "r") as f:
    content = f.read()

# Replace use_s3=True with use_s3=(req.upload_method == "s3") in generate_video, extend_video, edit_video
content = content.replace("use_s3=True)", 'use_s3=(req.upload_method == "s3"))')

# Also handle the _get_preprocessed_s3_url vs _get_preprocessed_base64_data_url logic.
def s3_vs_base64_logic(match):
    return """                        if req.upload_method == "s3":
                            url = await _get_preprocessed_s3_url(m_ref.id, constraints, expiration=300)
                        else:
                            url = _get_preprocessed_base64_data_url(m_ref.id, constraints)"""

content = re.sub(
    r'                        url = await _get_preprocessed_s3_url\(m_ref\.id, constraints, expiration=300\)',
    s3_vs_base64_logic,
    content
)

with open("routes/studio/studio_routes.py", "w") as f:
    f.write(content)
