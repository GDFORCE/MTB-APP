/* Read-only TypeScript source annotation using the project's installed parser. */
const fs = require('fs');
const path = require('path');
const root = process.argv[2];
const ts = require(path.join(root, 'frontend/node_modules/typescript/lib/typescript.js'));
const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const short = (s, limit = 320) => s.replace(/\s+/g, ' ').trim().slice(0, limit);
const answer = {};
for (const file of files) {
  const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/^\uFEFF/, '');
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  if (sf.parseDiagnostics.length) throw new Error(`Parse errors in ${file}`);
  const lines = source.split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  const nodes = [], symbols = [];
  const text = n => n ? short(n.getText(sf)) : '';
  const kinds = new Set([
    'ImportDeclaration', 'FunctionDeclaration', 'ArrowFunction', 'FunctionExpression',
    'VariableDeclaration', 'ExpressionStatement', 'ReturnStatement', 'IfStatement',
    'ForOfStatement', 'ForStatement', 'WhileStatement', 'TryStatement', 'CatchClause',
    'ThrowStatement', 'TypeAliasDeclaration', 'InterfaceDeclaration', 'PropertySignature',
    'PropertyAssignment', 'JsxOpeningElement', 'JsxSelfClosingElement', 'JsxClosingElement',
    'JsxAttribute', 'JsxText', 'JsxExpression', 'CallExpression', 'AwaitExpression',
    'ConditionalExpression', 'BinaryExpression', 'SpreadAssignment', 'ExportDeclaration',
  ]);
  function visit(n, scope) {
    const kind = ts.SyntaxKind[n.kind];
    const start = sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
    const end = sf.getLineAndCharacterOfPosition(n.end).line + 1;
    let nextScope = scope;
    if (['FunctionDeclaration', 'ArrowFunction', 'FunctionExpression'].includes(kind)) {
      let name = n.name && text(n.name);
      if (!name && ts.isVariableDeclaration(n.parent)) name = text(n.parent.name);
      if (!name && ts.isCallExpression(n.parent)) name = `callback to ${text(n.parent.expression)}`;
      name ||= `callback at line ${start}`;
      nextScope = scope ? `${scope} > ${name}` : name;
      symbols.push({name: nextScope, start, end, kind, doc: ''});
    } else if (['TypeAliasDeclaration', 'InterfaceDeclaration'].includes(kind)) {
      symbols.push({name: text(n.name), start, end, kind, doc: ''});
    }
    if (kinds.has(kind)) nodes.push({n, kind, start, end, scope: nextScope || 'module'});
    ts.forEachChild(n, child => visit(child, nextScope));
  }
  visit(sf, '');
  function explain(item) {
    const {n, kind} = item;
    switch (kind) {
      case 'ImportDeclaration': return `Import ${text(n.importClause) || 'module side effects'} from ${text(n.moduleSpecifier)} for use in this file.`;
      case 'FunctionDeclaration': return `Define ${text(n.name)}(${n.parameters.map(p => text(p)).join(', ')}). The body runs when called; async functions return a Promise.`;
      case 'ArrowFunction': case 'FunctionExpression': return `Create a function with parameters ${n.parameters.map(p => text(p)).join(', ') || '(none)'}. The body is invoked by its caller or the receiving component/API.`;
      case 'VariableDeclaration': {
        const init = n.initializer;
        if (init && ts.isCallExpression(init) && text(init.expression) === 'useState') return `Declare React state ${text(n.name)} with initializer ${init.arguments.map(text).join(', ') || 'undefined'}. Its setter queues an update and a render.`;
        if (init && ts.isCallExpression(init) && text(init.expression) === 'useRef') return `Create the persistent mutable React ref ${text(n.name)}; ref changes do not themselves trigger rendering.`;
        return `Declare ${text(n.name)}${n.type ? ` with TypeScript type ${text(n.type)}` : ''}${init ? ` and initialize it from ${text(init)}` : ' without an initializer'}.`;
      }
      case 'TypeAliasDeclaration': return `Define the compile-time type ${text(n.name)} as ${text(n.type)}. TypeScript types do not validate server responses at runtime.`;
      case 'InterfaceDeclaration': return `Define the compile-time object contract ${text(n.name)}; its members describe expected field types.`;
      case 'PropertySignature': return `Declare object field ${text(n.name)}${n.questionToken ? ' as optional' : ''} with type ${text(n.type)}. This is a type declaration, not a runtime assignment.`;
      case 'PropertyAssignment': return `Set object property ${text(n.name)} to ${text(n.initializer)} when this object is constructed.`;
      case 'ExpressionStatement': return `Evaluate ${text(n.expression)} for its effects; the expression's return value is discarded here.`;
      case 'ReturnStatement': return n.expression ? `Return ${text(n.expression)} and end this function invocation. In an async function the value resolves its Promise; JSX returns a description of the UI.` : 'End this function invocation with undefined.';
      case 'IfStatement': return `Execute the then branch when ${text(n.expression)} is truthy; execute the else branch, if present, otherwise.`;
      case 'ConditionalExpression': return `Evaluate ${text(n.condition)}; choose ${text(n.whenTrue)} when truthy, otherwise ${text(n.whenFalse)}.`;
      case 'BinaryExpression': return `Evaluate ${text(n.left)} ${text(n.operatorToken)} ${text(n.right)}. The operator shown in the source determines assignment, comparison, combination or short-circuit selection.`;
      case 'AwaitExpression': return `Wait for ${text(n.expression)} to settle. A rejection throws into the surrounding async function's error handling.`;
      case 'CallExpression': {
        const callee = text(n.expression);
        if (/\bapi\.(get|post|put|patch|delete)$/.test(callee)) return `Call ${callee} with ${n.arguments.map(text).join(', ')}. This sends an HTTP request; it does not itself calculate a schedule.`;
        return `Call ${callee} with ${n.arguments.map(text).join(', ') || 'no arguments'} and use the returned value in the surrounding expression.`;
      }
      case 'TryStatement': return 'Run the protected statements; a thrown error transfers control to catch, and finally executes on exit when present.';
      case 'CatchClause': return `Handle a thrown error${n.variableDeclaration ? ` bound to ${text(n.variableDeclaration.name)}` : ''}. Read this body to see which UI state/error is shown.`;
      case 'ThrowStatement': return `Throw ${text(n.expression)}; ordinary execution stops and the nearest matching error handler receives the error.`;
      case 'ForOfStatement': return `Iterate ${text(n.expression)}, binding each value to ${text(n.initializer)}.`;
      case 'ForStatement': return `Run the loop with initializer ${text(n.initializer)}, condition ${text(n.condition)} and update ${text(n.incrementor)}.`;
      case 'WhileStatement': return `Repeat the body while ${text(n.expression)} is truthy.`;
      case 'JsxOpeningElement': return `Open the ${text(n.tagName)} UI element and pass its attributes as props. Its children follow.`;
      case 'JsxSelfClosingElement': return `Render the ${text(n.tagName)} UI element with the props shown here and no children.`;
      case 'JsxClosingElement': return `Close the ${text(n.tagName)} UI element's children.`;
      case 'JsxAttribute': return `Pass prop ${text(n.name)}${n.initializer ? ` as ${text(n.initializer)}` : ' as true'}. A function-valued prop is passed for later invocation.`;
      case 'JsxText': return 'Literal text child inside JSX; it contributes displayed text after JSX whitespace handling.';
      case 'JsxExpression': return `Embed JavaScript ${text(n.expression) || 'comment content'} in JSX; the expression determines the child or prop value.`;
      case 'SpreadAssignment': return `Copy the enumerable properties of ${text(n.expression)} into this object at this position; later properties can overwrite them.`;
      default: return `${kind}: ${text(n)}. Follow the enclosing statement for its effect.`;
    }
  }
  const annotations = lines.map((line, index) => {
    const number = index + 1;
    const candidates = nodes.filter(n => n.start <= number && n.end >= number)
      .sort((a, b) => (a.end - a.start) - (b.end - b.start) || b.start - a.start || b.n.pos - a.n.pos);
    const owner = candidates[0];
    let note;
    const stripped = line.trim();
    if (!stripped) note = 'Blank line for readability; no runtime effect.';
    else if (/^(\/\/|\/\*|\*|\*\/)/.test(stripped)) note = 'Source comment/documentation. It is not executed; check the surrounding implementation before relying on historical claims.';
    else if (!owner) note = 'Structural delimiter or module-level syntax; read it with the adjacent declaration.';
    else {
      note = explain(owner);
      if (owner.start < number) note = `Continuation of the ${owner.kind} starting at L${owner.start}. ${note}`;
    }
    return {n: number, code: line, scope: owner ? owner.scope : 'module', kind: owner ? owner.kind : 'formatting', note};
  });
  answer[file] = {lines: annotations, symbols, parser: `TypeScript ${ts.version}`};
}
process.stdout.write(JSON.stringify(answer));
