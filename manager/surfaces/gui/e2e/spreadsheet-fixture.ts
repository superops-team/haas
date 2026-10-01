import { strToU8, zipSync } from "fflate";

const xml = (body: string) => strToU8(`<?xml version="1.0" encoding="UTF-8"?>${body}`);

const bytes = zipSync({
  "[Content_Types].xml": xml(
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
      '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
      "</Types>",
  ),
  "_rels/.rels": xml(
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
      "</Relationships>",
  ),
  "xl/workbook.xml": xml(
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
      '<sheets><sheet name="Summary" sheetId="1" r:id="rId1"/><sheet name="Detail" sheetId="2" r:id="rId2"/></sheets>' +
      "</workbook>",
  ),
  "xl/_rels/workbook.xml.rels": xml(
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
      '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>' +
      "</Relationships>",
  ),
  "xl/worksheets/sheet1.xml": xml(
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
      '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Summary value</t></is></c></row>' +
      '<row r="2"><c r="A2" t="inlineStr"><is><t>External link</t></is></c></row></sheetData>' +
      '<hyperlinks><hyperlink ref="A2" r:id="rId1"/></hyperlinks></worksheet>',
  ),
  "xl/worksheets/_rels/sheet1.xml.rels": xml(
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://workbook.invalid/blocked" TargetMode="External"/>' +
      "</Relationships>",
  ),
  "xl/worksheets/sheet2.xml": xml(
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
      '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Detail value</t></is></c></row></sheetData>' +
      "</worksheet>",
  ),
});

export const spreadsheetFixture = {
  base64: Buffer.from(bytes).toString("base64"),
  byteLength: bytes.byteLength,
};
