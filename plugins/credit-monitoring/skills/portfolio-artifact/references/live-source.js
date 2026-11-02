/* HOSTED_SOURCE_BEGIN */
/* The live dashboard's source block. `hosted_artifact.py build --drive-id
   --folder` writes it into the template in place of the Cowork block, with the
   book embedded and the two pins filled. Shipped empty: no drive or folder is
   ever named here or in the template.

   The page draws the embedded book first (`first`), then reads the store in
   SharePoint through the reader's own Microsoft 365 connector on every open. */
window.PM = window.PM || {};
PM.embedded = {};

PM.store = (function () {
  'use strict';
  /* The connector is named by its display name. There is no id in this
     mechanism. */
  var SERVER = 'Microsoft 365';
  /* The dashboard folder in SharePoint: the drive, and the folder that holds
     index.json, relative to the drive root without the document-library
     segment. The page reads known names there and never searches. */
  var DRIVE_ID = '';
  var FOLDER = '';

  /* A text file comes back in pages of about 200,000 characters. Each page of a
     paged file ends in a footer after a blank line, page 1's naming the file's
     bytes and "re-call with startPage=2", the last page's "last page". A page
     ending in one newline and "[truncated at N chars ...]" has more after it.
     Strip the footer and the newlines before it, and nothing else: a page can
     start with a space that is file text. The joined file must weigh what page
     1's footer names. */
  var FOOTER = /(?:\r?\n){1,2}(\[(?:truncated at [\d,]+ chars\b|page \d+(?: of \d+)? — lines \d+)[^\[\]\r\n]*\])\s*$/;
  var FOOTER_MORE = /\bstartPage\b|^\[truncated at /;
  var FOOTER_BYTES = /\bof a ([\d,]+)-byte file\b/;
  var MAX_PAGES = 40;

  var bridge = null;              // the resolved mcp namespace, once
  var linked = null;              // listTools' authStatus for SERVER, or 'absent'
  var reads = new Map();          // whole-file reads in flight, joined

  function err(kind, message, said) {
    var e = new Error(message);
    e.kind = kind; e.live = true;
    if (said) e.said = shown(said);
    if (kind === 'missing') e.absent = true;
    return e;
  }

  function connect() {
    if (bridge) return bridge;
    bridge = Promise.resolve().then(function () {
      if (!(window.claude && typeof window.claude.use === 'function')) {
        throw err('no-bridge', 'this page is not running inside Claude');
      }
      return window.claude.use('mcp');
    }).then(function (mcp) {
      if (!mcp || typeof mcp.callTool !== 'function') {
        throw err('not-granted', 'this page was not given Microsoft 365');
      }
      // Asked once and never waited on: it only picks which failure is named.
      if (typeof mcp.listTools === 'function') {
        mcp.listTools().then(function (r) {
          var servers = (r && r.servers) || [];
          for (var i = 0; i < servers.length; i++) {
            if (servers[i] && servers[i].server === SERVER) { linked = servers[i].authStatus; return; }
          }
          linked = 'absent';
        }, function () {});
      }
      return mcp;
    });
    bridge = bridge.catch(function (e) { bridge = null; throw e; });
    return bridge;
  }

  function said(e) {
    try { return String((e && e.message) || ''); } catch (x) { return ''; }
  }

  /* The connector refuses with a JSON object; the reader is shown its message,
     not its codes and request details. */
  function shown(text) {
    try {
      var o = JSON.parse(text);
      var m = o && (o.message || (o.error && o.error.message));
      if (typeof m === 'string' && m) return m;
    } catch (x) {}
    return text;
  }

  /* Name a rejected call. The runtime's code decides when it has one; a
     `tool_error` is the connector's own refusal, and its message is the only
     thing that tells the causes apart. */
  function classify(e, name) {
    var code = (e && e.code) || '';
    var text = said(e);
    if (code === 'approval_required'
        || /per-call approval|requires approval|approval[^.]*not[^.]*supported/i.test(text)) {
      return err('needs-approval', 'Microsoft 365 asks before every read, and this page cannot ask', text);
    }
    if (/^(?:needs_reauth|server_not_connected|selection_required|server_not_found)$/.test(code)
        || /unauthenticated|invalidAuthenticationToken|\b401\b/i.test(text)
        || linked === 'absent' || linked === 'needs_reauth') {
      return err('not-linked', 'Microsoft 365 is not connected for this reader', text);
    }
    if (/^(?:not_granted|capability_disabled|capability_removed|not_in_manifest|consent_required)$/.test(code)) {
      return err('not-granted', 'this page is not allowed to use Microsoft 365 for this reader', text);
    }
    if (code === 'blocked_by_policy' || /accessDenied|forbidden|\b403\b/i.test(text)) {
      return err('no-access', 'this account cannot open the dashboard folder', text);
    }
    if (/not_?found|item not found|does not exist|\b404\b/i.test(text)) {
      return err('missing', 'the dashboard folder does not hold ' + name, text);
    }
    return err('unavailable', 'the dashboard folder could not be read', text);
  }

  function extractText(r) {
    if (typeof r === 'string') return r;
    var sc = r && r.structuredContent;
    if (sc && typeof sc.content === 'string') return sc.content;
    if (r && Array.isArray(r.content)) {
      return r.content.filter(function (c) { return c && typeof c.text === 'string'; })
        .map(function (c) { return c.text; }).join('\n');
    }
    return (r && typeof r.content === 'string') ? r.content : '';
  }

  function morePages(r, footer, page) {
    var sc = r && r.structuredContent;
    if (sc && typeof sc === 'object') {
      var total = Number(sc.totalPages || sc.total_pages || sc.pageCount || 0);
      if (total) return page < total;
      if (sc.hasMore === true || sc.has_more === true) return true;
    }
    return FOOTER_MORE.test(footer);
  }

  /* One call, retried once when the runtime stamps the failure retryable. */
  function call(mcp, args) {
    function once() {
      return PM.sched.call('read|' + JSON.stringify(args), function () {
        return mcp.callTool(SERVER, 'read_resource', args);
      });
    }
    return once().catch(function (e) {
      if (!(e && e.retryable === true)) throw e;
      var wait = Math.min(Number(e.retryAfterMs) || 0, 60000) + 500 + Math.random() * 1000;
      return new Promise(function (ok) { setTimeout(ok, wait); }).then(once);
    });
  }

  function readPages(mcp, uri, name) {
    var parts = [];
    var fileBytes = null;
    var firstFooter = '';
    function next(page, last) {
      var args = page === 1 ? { uri: uri } : { uri: uri, startPage: page };
      return call(mcp, args).then(function (r) {
        var text = extractText(r);
        if (!text) {
          if (page === 1) throw err('missing', 'the dashboard folder answered with nothing for ' + name);
          throw err('cut-short', 'SharePoint handed over the first part of ' + name + ' and nothing after it');
        }
        var hit = FOOTER.exec(text);
        var footer = hit ? hit[1] : '';
        var body = hit ? text.slice(0, hit.index) : text;
        var more = morePages(r, footer, page);
        var bytesHit = page === 1 && FOOTER_BYTES.exec(footer);
        if (bytesHit) {
          fileBytes = Number(bytesHit[1].replace(/,/g, ''));
          firstFooter = footer;
        }
        if (page > 1 && body === last) {
          throw err('cut-short', 'SharePoint handed over the first part of ' + name + ' and cut the rest', footer);
        }
        parts.push(body);
        if (!more) {
          var joined = parts.join('');
          if (fileBytes !== null && new TextEncoder().encode(joined).length !== fileBytes) {
            throw err('cut-short', 'SharePoint handed over ' + name + ' in pages that do not add up '
                      + 'to the whole file', firstFooter);
          }
          return joined;
        }
        if (page >= MAX_PAGES) {
          throw err('cut-short', 'SharePoint handed over the first ' + page + ' pages of ' + name
                    + ' and cut the rest', footer);
        }
        return next(page + 1, body);
      });
    }
    return next(1, null);
  }

  /* A file the index names, by its path from the dashboard folder. A flat name
     today; a per-run folder's path once the row names one. `.jsonl` is never
     read: read_resource refuses it by type. */
  function refusal(name) {
    if (typeof name !== 'string' || !name) return 'an empty name';
    if (/\.jsonl$/i.test(name)) return 'a .jsonl file, which the connector refuses';
    if (/^\/|\\|[\x00-\x1f]/.test(name)) return 'an unsafe name';
    if (name.split('/').some(function (s) { return !s || s === '.' || s === '..'; })) return 'an unsafe name';
    return '';
  }

  function readText(name) {
    var refused = refusal(name);
    if (refused) return Promise.reject(err('refused', 'the dashboard does not read ' + refused + ': ' + name));
    if (!DRIVE_ID || !FOLDER) {
      return Promise.reject(err('unpinned', 'this page was built without a SharePoint folder'));
    }
    var uri = 'file:///' + DRIVE_ID + '/' + FOLDER + '/' + name;
    if (reads.has(uri)) return reads.get(uri);
    var p = connect().then(function (mcp) {
      return readPages(mcp, uri, name);
    }).catch(function (e) {
      if (e && e.live) throw e;
      throw classify(e, name);
    });
    var joined = p.finally(function () { reads.delete(uri); });
    reads.set(uri, joined);
    return joined;
  }

  return { readText: readText, connect: connect };
})();

PM.live = {
  readText: PM.store.readText,
  readJson: function (name) {
    return PM.store.readText(name).then(function (t) {
      try { return JSON.parse(t); }
      catch (x) {
        var e = new Error('the folder\'s copy of ' + name + ' is not readable');
        e.kind = 'unreadable'; e.live = true;
        throw e;
      }
    });
  },
  /* The book as it was when this page was built: its first screen. */
  first: {
    readText: function (name) {
      if (!Object.prototype.hasOwnProperty.call(PM.embedded, name)) {
        var e = new Error('the hosted book does not hold this file: ' + name);
        e.kind = 'unavailable';
        return Promise.reject(e);
      }
      return Promise.resolve(PM.embedded[name]);
    },
    readJson: function (name) { return this.readText(name).then(JSON.parse); }
  }
};
PM.source = function () { return PM.live; };
/* HOSTED_SOURCE_END */
