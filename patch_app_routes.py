# patch_app_routes.py
import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

tracking_routes = '''
@app.route('/config_proposals/tracking/<int:tracking_id>/edit', methods=['POST'])
def edit_proposal_order_tracking(tracking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    log = OrderTracking.query.get_or_404(tracking_id)
    if log.updated_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền sửa ghi chú này.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))
    
    note = request.form.get('note')
    if note is not None:
        log.note = note
        from datetime import datetime
        log.edited_at = datetime.utcnow()
        db.session.commit()
        flash('Đã sửa ghi chú bổ sung.', 'success')
    return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))

@app.route('/config_proposals/tracking/<int:tracking_id>/delete', methods=['POST'])
def delete_proposal_order_tracking(tracking_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    log = OrderTracking.query.get_or_404(tracking_id)
    if log.updated_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền xóa ghi chú này.', 'danger')
        return redirect(url_for('config_proposal_detail', proposal_id=log.proposal_id))
    
    p_id = log.proposal_id
    db.session.delete(log)
    db.session.commit()
    flash('Đã xóa ghi chú bổ sung.', 'success')
    return redirect(url_for('config_proposal_detail', proposal_id=p_id))
'''

if 'def edit_proposal_order_tracking' not in content:
    content = content.replace("def add_proposal_order_tracking(proposal_id):", tracking_routes + "\n@app.route('/config_proposals/<int:proposal_id>/add_tracking', methods=['POST'])\ndef add_proposal_order_tracking(proposal_id):")

bug_routes = '''
@app.route('/bug_reports/comments/<int:comment_id>/edit', methods=['POST'])
def edit_bug_report_comment(comment_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    comment = BugReportComment.query.get_or_404(comment_id)
    if comment.created_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền sửa bình luận này.', 'danger')
        return redirect(url_for('bug_report_detail', id=comment.bug_report_id))
    
    new_text = request.form.get('comment')
    if new_text and new_text.strip():
        comment.comment = new_text.strip()
        from datetime import datetime
        comment.edited_at = datetime.utcnow()
        db.session.commit()
        flash('Đã sửa bình luận.', 'success')
    return redirect(url_for('bug_report_detail', id=comment.bug_report_id))

@app.route('/bug_reports/comments/<int:comment_id>/delete', methods=['POST'])
def delete_bug_report_comment(comment_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    comment = BugReportComment.query.get_or_404(comment_id)
    if comment.created_by != session['user_id'] and session.get('role') != 'admin':
        flash('Bạn không có quyền xóa bình luận này.', 'danger')
        return redirect(url_for('bug_report_detail', id=comment.bug_report_id))
    
    b_id = comment.bug_report_id
    db.session.delete(comment)
    db.session.commit()
    flash('Đã xóa bình luận.', 'success')
    return redirect(url_for('bug_report_detail', id=b_id))
'''

if 'def edit_bug_report_comment' not in content:
    content = content.replace("def add_bug_report_comment(id):", bug_routes + "\n@app.route('/bug_reports/<int:id>/comment', methods=['POST'])\ndef add_bug_report_comment(id):")

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Routes added!')
