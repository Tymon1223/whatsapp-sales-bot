const BOT_SHARED_SECRET = 'change_me_shared_secret';

function doGet() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheet =
    spreadsheet.getSheetByName('Products') ||
    spreadsheet.getSheets()[0];

  if (!sheet) {
    return jsonResponse({
      ok: false,
      error: 'No sheets found in this spreadsheet'
    });
  }

  const rows = sheet.getDataRange().getValues();
  if (rows.length < 2) {
    return jsonResponse({
      ok: true,
      products: []
    });
  }

  const headers = rows[0].map(String);
  const products = rows.slice(1)
    .filter(row => row.some(cell => String(cell).trim() !== ''))
    .map(row => {
      const item = {};
      headers.forEach((header, index) => {
        item[header] = row[index];
      });
      return item;
    });

  return jsonResponse({
    ok: true,
    sheetName: sheet.getName(),
    products: products
  });
}

function doPost(e) {
  const payload = JSON.parse((e.postData && e.postData.contents) || '{}');

  if (payload.action === 'log_client' || payload.action === 'log_payment') {
    return handleClientLog(payload);
  }

  return jsonResponse({
    ok: false,
    error: 'Unsupported action'
  });
}

function handleClientLog(payload) {
  if (payload.secret !== BOT_SHARED_SECRET) {
    return jsonResponse({
      ok: false,
      error: 'Unauthorized'
    });
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheetName = payload.clientsSheetName || 'Clients';
  const sheet =
    spreadsheet.getSheetByName(sheetName) ||
    spreadsheet.insertSheet(sheetName);

  const headers = [
    'Created At',
    'Status',
    'Customer Full Name',
    'Customer Phone',
    'Delivery Address',
    'Product Name',
    'Order Color',
    'Order Quantity',
    'Amount',
    'Payment Method',
    'Remote Kaspi Phone',
    'WhatsApp Name',
    'Chat ID',
    'Receipt Type',
    'Receipt URL',
    'Receipt Caption',
    'Receipt File Name',
    'Notes'
  ];

  ensureHeaders(sheet, headers);
  sheet.appendRow([
    new Date(),
    payload.status || '',
    payload.customerFullName || '',
    payload.customerPhone || '',
    payload.deliveryAddress || '',
    payload.productName || '',
    payload.orderColor || '',
    payload.orderQuantity || '',
    payload.amount || '',
    payload.paymentMethod || '',
    payload.remoteKaspiPhone || '',
    payload.whatsAppName || '',
    payload.chatId || '',
    payload.receiptType || '',
    payload.receiptUrl || '',
    payload.receiptCaption || '',
    payload.receiptFileName || '',
    payload.notes || ''
  ]);

  return jsonResponse({
    ok: true,
    row: sheet.getLastRow()
  });
}

function ensureHeaders(sheet, headers) {
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
    return;
  }

  const firstRow = sheet.getRange(1, 1, 1, headers.length).getValues()[0];
  const isEmpty = firstRow.every(cell => String(cell).trim() === '');

  if (isEmpty) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
