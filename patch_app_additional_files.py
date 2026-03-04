# patch_app_additional_files.py
import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

additional_actions = """        # Upload additional files
        elif action in ['upload_files_purchasing', 'upload_files_payment', 'upload_files_receiving', 'upload_files_handover', 'upload_files_invoice']:
            step_map = {
                'upload_files_purchasing': ('purchasing', 'config_proposals.execute_purchase'),
                'upload_files_payment': ('payment', 'config_proposals.execute_accounting'),
                'upload_files_receiving': ('receiving', 'config_proposals.confirm_delivery'),
                'upload_files_handover': ('handover', 'config_proposals.confirm_delivery'),
                'upload_files_invoice': ('invoice', 'config_proposals.execute_accounting')
            }
            step, num_perm = step_map[action]
            if num_perm not in session.get('permissions', []) and session.get('role') != 'admin':
                flash('Bạn không có quyền tải thêm file cho bước này.', 'danger')
                return redirect(url_for('config_proposal_detail', proposal_id=p.id))
            
            handle_attachments(step)
            db.session.commit()
            flash('Đã tải thêm file đính kèm thành công.', 'success')
"""

# Insert right after `elif action == 'confirm_invoice': ... db.session.commit()`
# The end of `confirm_invoice` block is:
#             if all_done:
#                 p.status = 'completed'
#             db.session.commit()
#             flash('Đã xác nhận nhận hóa đơn và chứng từ...', 'success')

# Let's find: `flash('Đã xác nhận nhận hóa đơn và chứng từ liên quan', 'success')`
marker = "flash('Đã xác nhận nhận hóa đơn và chứng từ liên quan', 'success')"
if 'upload_files_purchasing' not in content:
    content = content.replace(marker, marker + '\n\n' + additional_actions)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("App patched for additonal files!")
