# patch_html_additional.py
import re

with open('templates/config_proposal_detail.html', 'r', encoding='utf-8') as f:
    html = f.read()

# For each section, we want to append an upload form after the attachments loop DIV if the step is finished.
# Example: 
#               <div class="mt-2">
#                 {% for attach in p.attachments %}
#                 {% if attach.step == 'invoice' %}
#                 <div class="small"><a href="{{ url_for('download_proposal_attachment', attachment_id=attach.id) }}"
#                     target="_blank" class="text-decoration-none"><i class="bi bi-paperclip"></i> {{ attach.file_name
#                     }}</a></div>
#                 {% endif %}
#                 {% endfor %}
#               </div>
#             </div>
#             {% if not p.invoice_received_at ...

# Let's write a targeted replace for each. We know the exact step mappings:
mappings = {
    'purchasing': ('p.purchasing_at', 'config_proposals.execute_purchase', 'upload_files_purchasing'),
    'payment': ('p.payment_at', 'config_proposals.execute_accounting', 'upload_files_payment'),
    'receiving': ('p.goods_received_at', 'config_proposals.confirm_delivery', 'upload_files_receiving'),
    'handover': ('p.handover_to_user_at', 'config_proposals.confirm_delivery', 'upload_files_handover'),
    'invoice': ('p.invoice_received_at', 'config_proposals.execute_accounting', 'upload_files_invoice')
}

for step, (date_field, permission, action) in mappings.items():
    # Find the block where `{% if attach.step == 'step' %}` exists until `</div>` that closes that loop.
    # We can inject our form right before the `</div>` that closes the row/item block, which is just before the `{% if not p.purchasing_at` block.
    
    # Actually, simpler: search for `{% if not <date_field> and ('<permission>' in current_permissions or`
    # and prepend our form wrapped in `{% if <date_field> and ('<permission>' ...) %}`
    
    # We can just match the `{% if not <date_field>` string.
    pattern = rf'(\{{% if not {date_field} and \(\'{permission}\' in current_permissions)'
    
    additional_form = f'''
            {{% if {date_field} and ('{permission}' in current_permissions or current_user.role == 'admin') %}}
            <form method="POST" action="{{{{ url_for('proposal_action', proposal_id=p.id) }}}}" class="d-inline mt-2" enctype="multipart/form-data">
              <div class="input-group input-group-sm mb-2" style="max-width:300px;">
                <input type="file" name="attachments" multiple class="form-control" accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg" required>
                <button type="submit" name="action" value="{action}" class="btn btn-outline-secondary">Tải thêm</button>
              </div>
            </form>
            {{% endif %}}
            '''
    html = re.sub(pattern, additional_form + r'\1', html)

with open('templates/config_proposal_detail.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('HTML additional files form inserted!')
