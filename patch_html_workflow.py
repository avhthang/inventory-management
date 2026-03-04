# patch_html_workflow.py
import re

with open("templates/config_proposal_detail.html", "r", encoding="utf-8") as f:
    html = f.read()

# Replace steps array
new_steps = """      {% set steps = [
      ('new', 'Mới tạo'),
      ('team_approved', 'Phê duyệt Trưởng BP'),
      ('it_consulted', 'Tư vấn IT'),
      ('finance_reviewed', 'Phê duyệt TC'),
      ('approved', 'Giám đốc phê duyệt')
      ] %}"""
html = re.sub(r'\{%\s*set steps = \[\s*\(\'new\', \'Mới\'\),[\s\S]*?\(\'approved\', \'Duyệt GĐ\'\)\s*\]\s*%\}', new_steps, html)

# Replace the badge logic and display
# We want to replace from `{# Badge Logic:` to `{% endfor %}`
# Actually, it's safer to find the loop body.
# Let's replace the whole `div.text-center` block in the loop.

old_loop_tail_regex = r'(<div class="text-center" style="width: 80px;.*?>)([\s\S]*?)(</div>\s*{% endfor %})'

# In the new code, `current_step_idx` indicates the currently active step (chưa duyệt / đang duyệt).
# Wait, "Đến bước nào thì hiển thị xanh bước đó" implies current step is ALSO green? Or current step is green only if it's completed?
# "Đến bước nào thì hiển thị xanh bước đó,, bước chưa duyệt vẫn để như ban đầu."
# So completed steps = green. Current active step = green or primary? Let's make `loop.index0 <= current_step_idx` green.

new_loop_tail = '''<div class="text-center" style="width: 18%; position: relative;">
            {# Badge Logic: Past=Success, Current=Success, Future=Secondary #}
            {% set badge_class = 'bg-secondary' %}
            {% set text_class = 'text-muted' %}

            {% if loop.index0 <= current_step_idx %} 
              {% set badge_class='bg-success text-white' %} 
              {% set text_class='text-success fw-bold' %} 
            {% endif %}

            <span class="badge rounded-pill {{ badge_class }} shadow-sm"
              style="width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center; margin: 0 auto; font-size: 1rem; position: relative; z-index: 2;">
              {% if loop.index0 < current_step_idx %}<i class="bi bi-check"></i>{% else %}{{ loop.index }}{% endif %}
            </span>
            <div class="mt-2 text-center" style="line-height: 1.2;">
                <div class="small {{ text_class }}" style="font-size: 0.8rem;">{{ s_label }}</div>
                {% if step_handler and step_handler != '---' %}
                <div class="text-muted" style="font-size: 0.7rem; margin-top: 4px;"><i class="bi bi-person"></i> {{ step_handler }}</div>
                {% endif %}
                {% if step_info and step_info != '---' %}
                <div class="text-muted" style="font-size: 0.7rem;"><i class="bi bi-clock"></i> {{ step_info }}</div>
                {% endif %}
            </div>
          </div>'''

# re.sub with dotall
html = re.sub(old_loop_tail_regex, r'\n          ' + new_loop_tail + r'\n          \3', html, flags=re.DOTALL)

# Remove the old info display area and script
old_info_script_regex = r'<!-- Info Display Area \(Dynamic\) -->[\s\S]*?</script>'
html = re.sub(old_info_script_regex, '', html)

with open("templates/config_proposal_detail.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Workflow HTML patched!")
